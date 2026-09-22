from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import requests
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.security import decrypt_secret, encrypt_secret
from app.models.integration import IntegrationConnection, IntegrationCredential
from app.services.backup_schedule_service import get_schedule_status

PROVIDER = "jianguoyun_webdav"
MODE = "backup"
DEFAULT_BASE_URL = "https://dav.jianguoyun.com/dav/"
DEFAULT_PREFIX = "ecommerce-workspace/webdav"


def _connection(db: Session) -> IntegrationConnection | None:
    return db.query(IntegrationConnection).filter_by(provider=PROVIDER, mode=MODE).first()


def _credential(db: Session) -> IntegrationCredential | None:
    return (
        db.query(IntegrationCredential)
        .filter_by(provider=PROVIDER)
        .order_by(IntegrationCredential.id.desc())
        .first()
    )


def _normalize_base_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("WebDAV 服务器地址不能为空")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("WebDAV 服务器地址必须是有效的 https:// 地址")
    return value.rstrip("/") + "/"


def _normalize_prefix(value: str) -> str:
    prefix = value.strip().strip("/") or DEFAULT_PREFIX
    if ".." in prefix.split("/") or "\\" in prefix:
        raise ValueError("WebDAV 远程目录不能包含 .. 或反斜杠")
    return prefix


def _username_hint(username: str) -> str:
    if "@" not in username:
        return f"••••{username[-4:]}" if len(username) >= 4 else "已保存"
    local, domain = username.split("@", 1)
    return f"{local[:2]}***@{domain}" if local else f"***@{domain}"


def get_config(db: Session) -> dict[str, Any]:
    conn = _connection(db)
    cred = _credential(db)
    meta = (conn.meta or {}) if conn else {}
    username = str(meta.get("username") or "")
    configured = bool(
        cred
        and username
        and str(meta.get("base_url") or "").strip()
    )
    return {
        "configured": configured,
        "enabled": bool(meta.get("enabled")) if configured else False,
        "baseUrl": str(meta.get("base_url") or DEFAULT_BASE_URL),
        "remotePath": str(meta.get("prefix") or DEFAULT_PREFIX),
        "username": username,
        "usernameHint": cred.key_hint if cred else "",
        "connectionStatus": conn.status if conn else "unconfigured",
        "lastTestedAt": conn.last_tested_at.isoformat() if conn and conn.last_tested_at else None,
        "lastSuccessAt": conn.last_success_at.isoformat() if conn and conn.last_success_at else None,
        "errorSummary": (conn.error_summary or None) if conn else None,
        "mode": MODE,
        "readEnabled": True,
        "scheduler": get_schedule_status("webdav"),
    }


def save_config(
    db: Session,
    *,
    base_url: str,
    remote_path: str,
    username: str,
    app_password: str,
    enabled: bool,
    actor: str,
) -> dict[str, Any]:
    base_url = _normalize_base_url(base_url)
    remote_path = _normalize_prefix(remote_path)
    username = username.strip()
    app_password = app_password.strip()
    if not username:
        raise ValueError("WebDAV 账号不能为空")

    cred = _credential(db)
    if not cred and not app_password:
        raise ValueError("首次配置必须填写坚果云应用密码")

    if app_password:
        password = app_password
    elif cred:
        try:
            previous = json.loads(decrypt_secret(cred.secret_encrypted))
            password = str(previous.get("password") or "")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("已保存的 WebDAV 凭据无法读取，请重新填写应用密码") from exc
        if not password:
            raise ValueError("WebDAV 应用密码为空，请重新填写")
    else:
        raise ValueError("首次配置必须填写坚果云应用密码")

    payload = json.dumps(
        {"username": username, "password": password},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if cred:
        cred.key_hint = _username_hint(username)
        cred.secret_encrypted = encrypt_secret(payload)
    else:
        cred = IntegrationCredential(
            provider=PROVIDER,
            key_hint=_username_hint(username),
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
        "base_url": base_url,
        "prefix": remote_path,
        "username": username,
        "enabled": bool(enabled),
        "access": "read_write",
        "remote_read": True,
    }
    db.commit()
    db.refresh(conn)

    audit(
        db,
        actor,
        "backup.webdav.config.save",
        "integration",
        conn.id,
        {
            "base_url": base_url,
            "prefix": remote_path,
            "username_hint": _username_hint(username),
            "enabled": bool(enabled),
            "access": "read_write",
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
        "base_url": public["baseUrl"],
        "prefix": public["remotePath"],
        "username": str(payload.get("username") or public["username"]),
        "password": str(payload.get("password") or ""),
    }


def _remote_url(base_url: str, remote_path: str, *, directory: bool = False) -> str:
    parts = [part for part in remote_path.strip("/").split("/") if part]
    encoded = "/".join(quote(part, safe="-_.~") for part in parts)
    url = base_url.rstrip("/") + "/"
    if encoded:
        url += encoded
    if directory and not url.endswith("/"):
        url += "/"
    return url


def _ensure_collections(session: requests.Session, base_url: str, prefix: str) -> None:
    current: list[str] = []
    for part in prefix.strip("/").split("/"):
        if not part:
            continue
        current.append(part)
        response = session.request(
            "MKCOL",
            _remote_url(base_url, "/".join(current), directory=True),
            timeout=(10, 30),
            allow_redirects=False,
        )
        if response.status_code not in {200, 201, 204, 405}:
            raise RuntimeError(f"WebDAV 创建远程目录失败（HTTP {response.status_code}）")


def _mark_test_failure(db: Session, message: str) -> None:
    conn = _connection(db)
    if not conn:
        return
    conn.status = "error"
    conn.last_tested_at = datetime.now(timezone.utc)
    conn.error_summary = message[:500]
    db.commit()


def test_connection(db: Session) -> dict[str, Any]:
    """验证坚果云 WebDAV 登录、写入、读取和清理，不触碰业务备份文件。"""
    config = runtime_config(db)
    if not config or not config.get("password"):
        raise ValueError("WebDAV 尚未完成配置")

    base_url = str(config["base_url"])
    prefix = str(config["prefix"])
    session = requests.Session()
    session.auth = (str(config["username"]), str(config["password"]))
    session.headers.update({"User-Agent": "ecommerce-workspace-backup/1", "Connection": "close"})
    try:
        propfind = session.request(
            "PROPFIND",
            base_url,
            headers={"Depth": "0", "Content-Length": "0"},
            data=b"",
            timeout=(10, 30),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        message = f"无法连接坚果云 WebDAV：{exc.__class__.__name__}"
        _mark_test_failure(db, message)
        raise RuntimeError(message) from exc

    if propfind.status_code in {401, 403}:
        message = "坚果云 WebDAV 鉴权失败，请检查账号和应用密码"
        _mark_test_failure(db, message)
        raise RuntimeError(message)
    if propfind.status_code not in {200, 207}:
        message = f"坚果云 WebDAV 连接测试失败（HTTP {propfind.status_code}）"
        _mark_test_failure(db, message)
        raise RuntimeError(message)

    probe_path = f"{prefix}/_healthcheck/write-probe-v1.txt"
    probe_url = _remote_url(base_url, probe_path)
    payload = b"ecommerce-workspace webdav write probe\n"
    try:
        _ensure_collections(session, base_url, f"{prefix}/_healthcheck")
        put = session.put(
            probe_url,
            data=payload,
            headers={"Content-Type": "text/plain", "Content-Length": str(len(payload))},
            timeout=(10, 60),
            allow_redirects=False,
        )
        if put.status_code not in {200, 201, 204}:
            raise RuntimeError(f"坚果云 WebDAV 写入测试失败（HTTP {put.status_code}）")
        read = session.get(probe_url, timeout=(10, 30), allow_redirects=False)
        if read.status_code != 200 or read.content != payload:
            raise RuntimeError("坚果云 WebDAV 读取校验失败")
        delete = session.delete(probe_url, timeout=(10, 30), allow_redirects=False)
        if delete.status_code not in {200, 204, 404}:
            raise RuntimeError(f"坚果云 WebDAV 探针清理失败（HTTP {delete.status_code}）")
    except requests.RequestException as exc:
        message = f"坚果云 WebDAV 读写测试失败：{exc.__class__.__name__}"
        _mark_test_failure(db, message)
        raise RuntimeError(message) from exc
    except RuntimeError as exc:
        _mark_test_failure(db, str(exc))
        raise

    conn = _connection(db)
    if conn:
        now = datetime.now(timezone.utc)
        conn.status = "connected"
        conn.last_tested_at = now
        conn.last_success_at = now
        conn.error_summary = ""
        db.commit()
    return {
        "ok": True,
        "status": propfind.status_code,
        "baseUrl": base_url,
        "remotePath": prefix,
        "message": "坚果云 WebDAV 连接、写入、读取和清理测试通过",
    }


def start_backup(db: Session) -> dict[str, Any]:
    config = get_config(db)
    if not config["configured"] or not config["enabled"]:
        raise ValueError("坚果云 WebDAV 尚未配置或当前未启用")

    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "webdav-backup.py"
    if not script.is_file():
        raise RuntimeError("WebDAV 备份执行器不存在")

    data_dir = Path(os.getenv("DATA_DIR") or (root / "data")).expanduser()
    log_dir = data_dir / "backup-jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "webdav-backup.log"
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
    return {"started": True, "target": "webdav", "mode": "full", "log": str(log_path)}
