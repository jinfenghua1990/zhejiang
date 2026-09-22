#!/usr/bin/env python3
"""坚果云 WebDAV 完整容灾备份上传器。

每次生成一个本地完整恢复点，再把所有组成文件上传到 WebDAV。
最后上传 snapshot.json，作为该恢复点已经完整提交的标记。
远端只使用配置的备份目录，不读取或删除其他目录。
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]


def load_simple_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def env_runtime_config() -> dict[str, Any] | None:
    if not truthy(os.getenv("WEBDAV_BACKUP_ENABLED")):
        return None
    names = {
        "base_url": "WEBDAV_BACKUP_URL",
        "username": "WEBDAV_BACKUP_USERNAME",
        "password": "WEBDAV_BACKUP_PASSWORD",
    }
    missing = [name for name in names.values() if not (os.getenv(name) or "").strip()]
    if missing:
        raise RuntimeError("WEBDAV_BACKUP_ENABLED=1，但缺少配置：" + ", ".join(missing))
    return {
        "configured": True,
        "enabled": True,
        "base_url": os.environ[names["base_url"]].strip().rstrip("/") + "/",
        "username": os.environ[names["username"]].strip(),
        "password": os.environ[names["password"]].strip(),
        "prefix": (os.getenv("WEBDAV_BACKUP_PREFIX") or "ecommerce-workspace/webdav").strip().strip("/"),
        "source": "environment",
    }


def database_runtime_config() -> dict[str, Any] | None:
    backend = ROOT / "backend"
    backend_text = str(backend)
    if backend_text not in sys.path:
        sys.path.insert(0, backend_text)
    from app.db import SessionLocal
    from app.services.webdav_backup_service import runtime_config

    db = SessionLocal()
    try:
        config = runtime_config(db)
    finally:
        db.close()
    if not config:
        return None
    return {**config, "source": "database"}


def resolve_runtime_config() -> dict[str, Any] | None:
    env_config = env_runtime_config()
    if env_config:
        return env_config
    return database_runtime_config()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_manifest(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "=" in raw:
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def backup_dir() -> Path:
    root = Path(os.getenv("PERSIST_ROOT") or str(ROOT)).expanduser()
    return Path(os.getenv("BACKUP_DIR") or (root / "backups")).expanduser()


def date_path(timestamp: str) -> str:
    if len(timestamp) >= 8:
        return f"{timestamp[:4]}/{timestamp[4:6]}/{timestamp[6:8]}"
    return "undated"


def remote_url(base_url: str, remote_path: str, *, directory: bool = False) -> str:
    parts = [part for part in remote_path.strip("/").split("/") if part]
    encoded = "/".join(quote(part, safe="-_.~") for part in parts)
    url = base_url.rstrip("/") + "/"
    if encoded:
        url += encoded
    if directory and not url.endswith("/"):
        url += "/"
    return url


def ensure_collections(session: requests.Session, base_url: str, remote_path: str) -> None:
    current: list[str] = []
    for part in remote_path.strip("/").split("/"):
        if not part:
            continue
        current.append(part)
        response = session.request(
            "MKCOL",
            remote_url(base_url, "/".join(current), directory=True),
            timeout=(15, 60),
            allow_redirects=False,
        )
        if response.status_code not in {200, 201, 204, 405}:
            raise RuntimeError(f"WebDAV 创建远程目录失败（HTTP {response.status_code}）")


def put_file(session: requests.Session, base_url: str, remote_path: str, source: Path) -> int:
    size = source.stat().st_size
    with source.open("rb") as stream:
        response = session.put(
            remote_url(base_url, remote_path),
            data=stream,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(size),
                "Expect": "",
                "Connection": "close",
            },
            timeout=(15, 7200),
            allow_redirects=False,
        )
    if response.status_code not in {200, 201, 204}:
        body = response.text[:500].replace("\n", " ")
        raise RuntimeError(f"WebDAV 上传失败 HTTP {response.status_code}：{body}")
    return response.status_code


def put_bytes(session: requests.Session, base_url: str, remote_path: str, payload: bytes) -> int:
    response = session.put(
        remote_url(base_url, remote_path),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
            "Expect": "",
            "Connection": "close",
        },
        timeout=(15, 120),
        allow_redirects=False,
    )
    if response.status_code not in {200, 201, 204}:
        body = response.text[:500].replace("\n", " ")
        raise RuntimeError(f"WebDAV 上传索引失败 HTTP {response.status_code}：{body}")
    return response.status_code


def component_paths(directory: Path, values: dict[str, str]) -> list[tuple[str, Path, str]]:
    specs = [
        ("base_manifest", "base_manifest", "base_manifest_sha256"),
        ("db", "db", "db_sha256"),
        ("data", "data", "data_sha256"),
        ("app", "app", "app_sha256"),
        ("config", "config", "config_sha256"),
        ("docker_image", "docker_image", "docker_image_sha256"),
    ]
    result: list[tuple[str, Path, str]] = []
    for role, name_key, hash_key in specs:
        name = values.get(name_key, "")
        expected = values.get(hash_key, "")
        if not name:
            continue
        if "/" in name or "\\" in name:
            raise RuntimeError(f"manifest 文件名非法：{name}")
        source = directory / name
        if not source.is_file():
            raise RuntimeError(f"完整恢复点缺少文件：{source}")
        actual = sha256_file(source)
        if expected and actual != expected:
            raise RuntimeError(f"本地 SHA256 校验失败：{source.name}")
        result.append((role, source, actual))
    return result


def prepare_full_snapshot(directory: Path) -> tuple[Path, dict[str, str]]:
    env = os.environ.copy()
    env["BACKUP_DIR"] = str(directory)
    env["FULL_BACKUP_INCLUDE_DOCKER_IMAGE"] = "auto"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "full-backup.sh")],
        cwd=str(ROOT),
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    sys.stdout.write(result.stdout)
    manifest_path = ""
    for line in result.stdout.splitlines():
        if line.startswith("FULL_BACKUP_MANIFEST="):
            manifest_path = line.split("=", 1)[1].strip()
    if not manifest_path:
        raise RuntimeError("完整容灾脚本没有返回 FULL_BACKUP_MANIFEST")
    manifest = Path(manifest_path)
    if not manifest.is_file():
        raise RuntimeError(f"完整容灾 manifest 不存在：{manifest}")
    return manifest, parse_manifest(manifest)


def _run_main() -> int:
    load_simple_env(ROOT / ".env")
    try:
        config = resolve_runtime_config()
    except Exception as exc:
        print(f"WebDAV 配置读取失败：{exc}", file=sys.stderr)
        return 2
    if not config:
        print("坚果云 WebDAV 尚未配置或环境未启用；跳过本次上传。")
        return 0
    if not bool(config.get("enabled")):
        print("坚果云 WebDAV 已配置但当前停用；跳过本次上传。")
        return 0

    directory = backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".cloud-backup.lock"
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("已有 R2/Kodo/WebDAV 云端备份任务在运行，本次 WebDAV 跳过。")
            return 0

        base_url = str(config["base_url"]).strip().rstrip("/") + "/"
        username = str(config["username"]).strip()
        password = str(config["password"])
        prefix = str(config.get("prefix") or "ecommerce-workspace/webdav").strip().strip("/")
        session = requests.Session()
        session.auth = (username, password)
        session.headers.update({"User-Agent": "ecommerce-workspace-backup/1"})
        try:
            propfind = session.request(
                "PROPFIND",
                base_url,
                headers={"Depth": "0", "Content-Length": "0"},
                data=b"",
                timeout=(15, 60),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"WebDAV 登录预检失败：{exc.__class__.__name__}") from exc
        if propfind.status_code in {401, 403}:
            raise RuntimeError("WebDAV 登录预检失败：账号或应用密码未被接受")
        if propfind.status_code not in {200, 207}:
            raise RuntimeError(f"WebDAV 登录预检失败（HTTP {propfind.status_code}）")

        manifest, values = prepare_full_snapshot(directory)
        timestamp = values.get("timestamp", "")
        if not timestamp:
            raise RuntimeError("完整容灾 manifest 缺少 timestamp")
        receipt_dir = directory / ".webdav-uploaded"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        receipt = receipt_dir / f"{timestamp}.json"
        if receipt.exists():
            print(f"该完整恢复点已有 WebDAV 成功记录，跳过重复上传：{receipt}")
            return 0

        snapshot_root = f"{prefix}/snapshots/{date_path(timestamp)}/{timestamp}"
        ensure_collections(session, base_url, snapshot_root)

        upload_files = component_paths(directory, values)
        upload_files.append(("full_manifest", manifest, sha256_file(manifest)))
        objects: list[dict[str, Any]] = []
        print(f"==> 坚果云 WebDAV 完整容灾备份：{manifest.name}")
        print(f"==> 配置来源：{config.get('source', 'database')}")

        for role, source, digest in upload_files:
            remote_path = f"{snapshot_root}/{source.name}"
            status = put_file(session, base_url, remote_path, source)
            objects.append(
                {
                    "role": role,
                    "localFile": source.name,
                    "remotePath": remote_path,
                    "size": source.stat().st_size,
                    "sha256": digest,
                    "status": status,
                }
            )
            print(f"上传：{source.name} -> {remote_path}")

        snapshot = {
            "version": 1,
            "kind": "full",
            "timestamp": timestamp,
            "mode": "webdav-full",
            "sourceManifest": manifest.name,
            "objects": objects,
            "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        snapshot_path = f"{snapshot_root}/snapshot.json"
        snapshot_status = put_bytes(
            session,
            base_url,
            snapshot_path,
            (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        snapshot["snapshotObjectKey"] = snapshot_path
        snapshot["snapshotStatus"] = snapshot_status
        receipt.write_text(
            json.dumps(
                {
                    **snapshot,
                    "baseUrl": base_url,
                    "remotePath": prefix,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"==> 完成：WebDAV 恢复点提交标记：{snapshot_path}")
        print(f"==> 本地上传记录：{receipt}")
        return 0


def _receipt_fingerprint(directory: Path) -> str:
    receipt_dir = directory / ".webdav-uploaded"
    if not receipt_dir.is_dir():
        return ""
    rows = sorted(receipt_dir.glob("*.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if not rows:
        return ""
    row = rows[0]
    return f"{row.name}:{row.stat().st_mtime_ns}"


def _write_job_status(
    directory: Path,
    *,
    status: str,
    started_at: str,
    finished_at: str | None = None,
    exit_code: int | None = None,
) -> None:
    status_dir = directory / ".job-status"
    status_dir.mkdir(parents=True, exist_ok=True)
    target = status_dir / "webdav.json"
    tmp = status_dir / ".webdav.json.tmp"
    payload = {
        "target": "webdav",
        "status": status,
        "mode": "full",
        "startedAt": started_at,
        "finishedAt": finished_at,
        "exitCode": exit_code,
    }
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def main() -> int:
    directory = backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    before = _receipt_fingerprint(directory)
    _write_job_status(directory, status="running", started_at=started)
    try:
        code = _run_main()
    except Exception:
        _write_job_status(
            directory,
            status="failed",
            started_at=started,
            finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            exit_code=2,
        )
        raise
    after = _receipt_fingerprint(directory)
    status = "success" if code == 0 and after != before else "skipped" if code == 0 else "failed"
    _write_job_status(
        directory,
        status=status,
        started_at=started,
        finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        exit_code=code,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
