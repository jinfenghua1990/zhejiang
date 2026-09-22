from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import requests

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.security import decrypt_secret, encrypt_secret
from app.models.integration import IntegrationConnection, IntegrationCredential
from app.services.backup_schedule_service import get_schedule_status

PROVIDER = "cloudflare_r2"
MODE = "backup"
DEFAULT_PREFIX = "ecommerce-workspace/backup"


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
        and str(meta.get("endpoint_url") or "").strip()
        and str(meta.get("bucket") or "").strip()
    )
    return {
        "configured": configured,
        "enabled": bool(meta.get("enabled")) if configured else False,
        "endpointUrl": str(meta.get("endpoint_url") or ""),
        "bucket": str(meta.get("bucket") or ""),
        "prefix": str(meta.get("prefix") or DEFAULT_PREFIX),
        "accessKeyHint": cred.key_hint if cred else "",
        "fullIntervalDays": int(meta.get("full_interval_days") or 10),
        "mode": MODE,
        "readEnabled": True,
        "scheduler": get_schedule_status("r2"),
    }


def save_config(
    db: Session,
    *,
    endpoint_url: str,
    bucket: str,
    prefix: str,
    access_key: str,
    secret_key: str,
    enabled: bool,
    full_interval_days: int,
    actor: str,
) -> dict[str, Any]:
    endpoint_url = endpoint_url.strip().rstrip("/")
    bucket = bucket.strip()
    prefix = prefix.strip().strip("/") or DEFAULT_PREFIX
    access_key = access_key.strip()
    secret_key = secret_key.strip()

    if not endpoint_url.startswith("https://"):
        raise ValueError("R2 Endpoint 必须使用 https://")
    if not bucket:
        raise ValueError("R2 Bucket 不能为空")
    if ".." in prefix.split("/"):
        raise ValueError("对象前缀不能包含 ..")
    if full_interval_days < 1 or full_interval_days > 365:
        raise ValueError("全量容灾间隔必须在 1-365 天之间")

    cred = _credential(db)
    if bool(access_key) != bool(secret_key):
        raise ValueError("Access Key ID 与 Secret Access Key 必须同时填写")
    if not cred and not (access_key and secret_key):
        raise ValueError("首次配置必须填写 Access Key ID 与 Secret Access Key")

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
        "endpoint_url": endpoint_url,
        "bucket": bucket,
        "prefix": prefix,
        "enabled": bool(enabled),
        "full_interval_days": int(full_interval_days),
        "access": "read_write",
    }
    db.commit()
    db.refresh(conn)

    audit(
        db,
        actor,
        "backup.r2.config.save",
        "integration",
        conn.id,
        {
            "endpoint_url": endpoint_url,
            "bucket": bucket,
            "prefix": prefix,
            "enabled": bool(enabled),
            "full_interval_days": int(full_interval_days),
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
        "endpoint_url": public["endpointUrl"],
        "access_key": str(payload.get("access_key") or ""),
        "secret_key": str(payload.get("secret_key") or ""),
    }


def _signing_key(secret_key: str, date_stamp: str) -> bytes:
    k_date = hmac.new(("AWS4" + secret_key).encode(), date_stamp.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, b"auto", hashlib.sha256).digest()
    k_service = hmac.new(k_region, b"s3", hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def test_connection(db: Session) -> dict[str, Any]:
    """用签名 HEAD Bucket 验证 R2 Endpoint/Bucket/凭据；不创建、修改或删除对象。"""
    config = runtime_config(db)
    if not config:
        raise ValueError("R2 尚未完成配置")

    endpoint_url = str(config["endpoint_url"]).strip().rstrip("/")
    bucket = str(config["bucket"]).strip()
    access_key = str(config["access_key"]).strip()
    secret_key = str(config["secret_key"]).strip()

    base = urlsplit(endpoint_url)
    if base.scheme != "https" or not base.netloc:
        raise ValueError("R2 Endpoint 必须是有效 https:// 地址")

    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(b"").hexdigest()

    path_prefix = base.path.rstrip("/")
    canonical_uri = quote(f"{path_prefix}/{bucket}", safe="/-_.~")
    canonical_headers = (
        f"host:{base.netloc}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        ["HEAD", canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date_stamp}/auto/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signature = hmac.new(
        _signing_key(secret_key, date_stamp),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    url = f"{endpoint_url}/{quote(bucket, safe='-_.~')}"
    headers = {
        "Authorization": authorization,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    try:
        response = requests.head(url, headers=headers, timeout=(10, 20), allow_redirects=False)
    except requests.RequestException as exc:
        raise RuntimeError(f"无法连接 R2：{exc.__class__.__name__}") from exc

    if 200 <= response.status_code < 300:
        return {
            "ok": True,
            "status": response.status_code,
            "bucket": bucket,
            "message": "R2 连接正常，Bucket 与凭据可访问",
        }
    if response.status_code in {401, 403}:
        raise RuntimeError("R2 鉴权失败，请检查 Access Key、Secret Key 与 Bucket 权限")
    if response.status_code == 404:
        raise RuntimeError("R2 Bucket 不存在，或 Endpoint / Bucket 名称不正确")
    raise RuntimeError(f"R2 连接测试失败（HTTP {response.status_code}）")


def start_backup(mode: str = "auto") -> dict[str, Any]:
    if mode not in {"auto", "daily", "full"}:
        raise ValueError("备份模式必须是 auto / daily / full")

    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "r2-backup.py"
    if not script.is_file():
        raise RuntimeError("R2 备份执行器不存在")

    data_dir = Path(os.getenv("DATA_DIR") or (root / "data")).expanduser()
    log_dir = data_dir / "backup-jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "r2-backup.log"
    log_stream = log_path.open("ab")
    try:
        subprocess.Popen(
            [sys.executable, str(script), "--mode", mode],
            cwd=str(root),
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
            start_new_session=True,
        )
    finally:
        log_stream.close()
    return {"started": True, "target": "r2", "mode": mode, "log": str(log_path)}

def start_restore_prepare(snapshot_object_key: str = "", *, prefix: str = DEFAULT_PREFIX) -> dict[str, Any]:
    """后台准备并验证 R2 全量恢复点；可指定历史 snapshot.json，不覆盖生产环境。"""
    snapshot_object_key = snapshot_object_key.strip()
    normalized_prefix = prefix.strip().strip("/") or DEFAULT_PREFIX
    if snapshot_object_key:
        expected_root = f"{normalized_prefix}/full/"
        if (
            snapshot_object_key.startswith("/")
            or "\\" in snapshot_object_key
            or ".." in snapshot_object_key.split("/")
            or not snapshot_object_key.startswith(expected_root)
            or not snapshot_object_key.endswith("/snapshot.json")
        ):
            raise ValueError("R2 恢复点路径无效或不属于当前备份前缀")
    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "r2-restore.py"
    if not script.is_file():
        raise RuntimeError("R2 恢复执行器不存在")

    data_dir = Path(os.getenv("DATA_DIR") or (root / "data")).expanduser()
    log_dir = data_dir / "backup-jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "r2-restore.log"
    log_stream = log_path.open("ab")
    try:
        command = [sys.executable, str(script)]
        if snapshot_object_key:
            command.extend(["--snapshot-key", snapshot_object_key])
        subprocess.Popen(
            command,
            cwd=str(root),
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
            start_new_session=True,
        )
    finally:
        log_stream.close()
    return {
        "started": True,
        "target": "r2",
        "action": "prepare_restore",
        "snapshotObjectKey": snapshot_object_key,
        "log": str(log_path),
    }
