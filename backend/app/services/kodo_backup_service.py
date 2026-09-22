from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.security import decrypt_secret, encrypt_secret
from app.models.integration import IntegrationConnection, IntegrationCredential
from app.services.backup_schedule_service import get_schedule_status

PROVIDER = "kodo_cold"
MODE = "upload_only"


def _connection(db: Session) -> IntegrationConnection | None:
    return db.query(IntegrationConnection).filter_by(provider=PROVIDER, mode=MODE).first()


def _credential(db: Session) -> IntegrationCredential | None:
    return (
        db.query(IntegrationCredential)
        .filter_by(provider=PROVIDER)
        .order_by(IntegrationCredential.id.desc())
        .first()
    )


def get_config(db: Session) -> dict[str, Any]:
    conn = _connection(db)
    cred = _credential(db)
    meta = (conn.meta or {}) if conn else {}
    configured = bool(
        cred
        and str(meta.get("bucket") or "").strip()
        and str(meta.get("upload_url") or "").strip()
    )
    return {
        "configured": configured,
        "enabled": bool(meta.get("enabled")) if configured else False,
        "bucket": str(meta.get("bucket") or ""),
        "uploadUrl": str(meta.get("upload_url") or ""),
        "prefix": str(meta.get("prefix") or "ecommerce-workspace/cold"),
        "accessKeyHint": cred.key_hint if cred else "",
        "mode": MODE,
        "readEnabled": False,
        "scheduler": get_schedule_status("kodo"),
    }


def save_config(
    db: Session,
    *,
    bucket: str,
    upload_url: str,
    prefix: str,
    access_key: str,
    secret_key: str,
    enabled: bool,
    actor: str,
) -> dict[str, Any]:
    bucket = bucket.strip()
    upload_url = upload_url.strip().rstrip("/")
    prefix = prefix.strip().strip("/") or "ecommerce-workspace/cold"
    access_key = access_key.strip()
    secret_key = secret_key.strip()

    if not bucket:
        raise ValueError("Bucket 不能为空")
    if not upload_url.startswith("https://"):
        raise ValueError("上传域名必须使用 https://")
    if ".." in prefix.split("/"):
        raise ValueError("对象前缀不能包含 ..")

    cred = _credential(db)
    if bool(access_key) != bool(secret_key):
        raise ValueError("Access Key 与 Secret Key 必须同时填写")
    if not cred and not (access_key and secret_key):
        raise ValueError("首次配置必须填写 Access Key 与 Secret Key")

    if access_key and secret_key:
        payload = json.dumps(
            {"access_key": access_key, "secret_key": secret_key},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if cred:
            cred.key_hint = f"••••{access_key[-4:]}" if len(access_key) >= 4 else "已保存"
            cred.secret_encrypted = encrypt_secret(payload)
        else:
            cred = IntegrationCredential(
                provider=PROVIDER,
                key_hint=f"••••{access_key[-4:]}" if len(access_key) >= 4 else "已保存",
                secret_encrypted=encrypt_secret(payload),
                extra={"mode": MODE},
            )
            db.add(cred)

    conn = _connection(db)
    if not conn:
        conn = IntegrationConnection(provider=PROVIDER, mode=MODE, phase=1)
        db.add(conn)
    conn.status = "configured"
    conn.error_summary = ""
    conn.meta = {
        "bucket": bucket,
        "upload_url": upload_url,
        "prefix": prefix,
        "enabled": bool(enabled),
        "access": "upload_only",
        "remote_read": False,
    }
    db.commit()
    db.refresh(conn)

    audit(
        db,
        actor,
        "backup.kodo.config.save",
        "integration",
        conn.id,
        {
            "bucket": bucket,
            "upload_url": upload_url,
            "prefix": prefix,
            "enabled": bool(enabled),
            "access": "upload_only",
        },
    )
    return get_config(db)


def runtime_config(db: Session) -> dict[str, Any] | None:
    public = get_config(db)
    if not public["configured"]:
        return None
    cred = _credential(db)
    if not cred:
        return None
    payload = json.loads(decrypt_secret(cred.secret_encrypted))
    return {
        **public,
        "access_key": str(payload.get("access_key") or ""),
        "secret_key": str(payload.get("secret_key") or ""),
    }



def _qiniu_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _upload_token(access_key: str, secret_key: str, bucket: str, object_key: str) -> str:
    policy = {
        "scope": f"{bucket}:{object_key}",
        "deadline": int(time.time()) + 600,
        "insertOnly": 1,
        "returnBody": '{"key":$(key),"hash":$(etag),"fsize":$(fsize)}',
    }
    encoded = _qiniu_b64(json.dumps(policy, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha1).digest()
    return f"{access_key}:{_qiniu_b64(signature)}:{encoded}"


def test_write_connection(db: Session) -> dict[str, Any]:
    """只写探针：验证 Kodo 上传凭据，不执行 GET/List/HEAD/Delete。"""
    config = runtime_config(db)
    if not config:
        raise ValueError("Kodo 冷备尚未完成配置")

    prefix = str(config.get("prefix") or "ecommerce-workspace/cold").strip().strip("/")
    object_key = f"{prefix}/_healthcheck/write-probe-v1.txt"
    token = _upload_token(
        str(config["access_key"]),
        str(config["secret_key"]),
        str(config["bucket"]),
        object_key,
    )
    payload = b"ecommerce-workspace cold-backup write probe\n"
    try:
        response = requests.post(
            str(config["uploadUrl"]).rstrip("/"),
            data={"token": token, "key": object_key},
            files={"file": ("write-probe-v1.txt", payload, "text/plain")},
            timeout=(10, 30),
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"无法连接 Kodo 上传域名：{exc.__class__.__name__}") from exc

    if 200 <= response.status_code < 300 or response.status_code in {409, 614}:
        return {
            "ok": True,
            "status": response.status_code,
            "bucket": config["bucket"],
            "message": "Kodo 只写测试通过；上传凭据可用，未执行任何远端读取",
        }
    if response.status_code in {401, 403}:
        raise RuntimeError("Kodo 上传鉴权失败，请检查 Access Key、Secret Key、Bucket 与上传域名")
    body = response.text[:300].replace("\n", " ")
    raise RuntimeError(f"Kodo 只写测试失败（HTTP {response.status_code}）：{body}")


def start_backup() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "kodo-cold-upload.py"
    if not script.is_file():
        raise RuntimeError("Kodo 冷备执行器不存在")

    data_dir = Path(os.getenv("DATA_DIR") or (root / "data")).expanduser()
    log_dir = data_dir / "backup-jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "kodo-cold-backup.log"
    log_stream = log_path.open("ab")
    try:
        subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(root),
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
            start_new_session=True,
        )
    finally:
        log_stream.close()
    return {"started": True, "target": "kodo", "mode": "full", "log": str(log_path)}
