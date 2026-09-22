#!/usr/bin/env python3
"""
七牛云 Kodo 国内冷备上传器（upload-only）。

安全边界：
- 只读取本地 BACKUP_DIR。
- 只向 Kodo 上传域名发送上传请求。
- 不调用下载、GET Object、List、Head、Stat、远端校验或恢复接口。
- 完整性校验在本地完成；内容对象按 SHA256 去重上传，每日 snapshot 最后上传作为恢复点提交标记。
- 去重判断只读取本地成功回执，不读取 Kodo；本地回执丢失时用 insertOnly 内容键安全重试。

默认关闭。优先读取系统里加密保存的 Kodo 配置；也保留 KODO_COLD_* 环境变量作为部署级兜底。
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]


def load_simple_env(path: Path) -> None:
    """仅补充当前进程中尚未存在的简单 KEY=VALUE；不执行 shell。"""
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
    """环境变量仅作为部署级兜底；只有显式启用时才使用。"""
    if not truthy(os.getenv("KODO_COLD_ENABLED")):
        return None
    names = {
        "bucket": "KODO_COLD_BUCKET",
        "upload_url": "KODO_COLD_UPLOAD_URL",
        "access_key": "KODO_COLD_ACCESS_KEY",
        "secret_key": "KODO_COLD_SECRET_KEY",
    }
    missing = [env_name for env_name in names.values() if not (os.getenv(env_name) or "").strip()]
    if missing:
        raise RuntimeError("KODO_COLD_ENABLED=1，但缺少配置：" + ", ".join(missing))
    return {
        "configured": True,
        "enabled": True,
        "bucket": os.environ[names["bucket"]].strip(),
        "upload_url": os.environ[names["upload_url"]].strip().rstrip("/"),
        "access_key": os.environ[names["access_key"]].strip(),
        "secret_key": os.environ[names["secret_key"]].strip(),
        "prefix": (os.getenv("KODO_COLD_PREFIX") or "ecommerce-workspace/cold").strip().strip("/"),
        "source": "environment",
    }


def database_runtime_config() -> dict[str, Any] | None:
    """读取系统 UI 保存的加密配置；仅在服务端解密，不把密钥输出到日志。"""
    backend = ROOT / "backend"
    backend_text = str(backend)
    if backend_text not in sys.path:
        sys.path.insert(0, backend_text)

    from app.db import SessionLocal
    from app.services.kodo_backup_service import runtime_config

    db = SessionLocal()
    try:
        config = runtime_config(db)
    finally:
        db.close()
    if not config:
        return None
    return {
        **config,
        "upload_url": str(config.get("uploadUrl") or "").strip().rstrip("/"),
        "source": "database",
    }


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
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_known_objects(receipt_dir: Path) -> dict[str, str]:
    """只从本地成功回执恢复 SHA256 -> 远端对象键映射，不访问 Kodo。"""
    known: dict[str, str] = {}
    if not receipt_dir.is_dir():
        return known
    for receipt in sorted(receipt_dir.glob("*.json")):
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = payload.get("objects") or payload.get("files") or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            digest = str(row.get("sha256") or "").lower()
            object_key = str(row.get("objectKey") or row.get("object_key") or "")
            if len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest) and object_key:
                known.setdefault(digest, object_key)
    return known


def content_object_key(prefix: str, digest: str) -> str:
    digest = digest.lower().strip()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("非法 SHA256")
    return f"{prefix}/objects/sha256/{digest[:2]}/{digest}"


def qiniu_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def upload_token(access_key: str, secret_key: str, bucket: str, object_key: str) -> str:
    # scope 精确到单个 object key，避免脚本生成可覆盖其他对象的宽泛上传凭证。
    policy = {
        "scope": f"{bucket}:{object_key}",
        "deadline": int(time.time()) + 3600,
        "insertOnly": 1,
        "returnBody": '{"key":$(key),"hash":$(etag),"fsize":$(fsize)}',
    }
    encoded_policy = qiniu_b64(
        json.dumps(policy, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        secret_key.encode("utf-8"),
        encoded_policy.encode("ascii"),
        hashlib.sha1,
    ).digest()
    return f"{access_key}:{qiniu_b64(signature)}:{encoded_policy}"


def upload_one(
    *,
    upload_url: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    object_key: str,
    source: Path,
) -> dict[str, Any]:
    token = upload_token(access_key, secret_key, bucket, object_key)
    # macOS/Python 3.14 在七牛简单表单上传较大文件时可能卡在 TLS 写入；
    # curl 使用系统网络栈稳定流式上传，Token 通过临时文件传入，不出现在命令行和日志中。
    if source.stat().st_size >= 4 * 1024 * 1024 and shutil.which("curl"):
        with tempfile.TemporaryDirectory(prefix="kodo-upload-") as temp_dir:
            token_path = Path(temp_dir) / "token"
            token_path.write_text(token, encoding="utf-8")
            token_path.chmod(0o600)
            result = subprocess.run(
                [
                    shutil.which("curl") or "curl",
                    "--silent",
                    "--show-error",
                    "--connect-timeout",
                    "15",
                    "--max-time",
                    "7200",
                    "--write-out",
                    "\nKODO_HTTP_STATUS=%{http_code}\n",
                    "-F",
                    f"token=<{token_path}",
                    "-F",
                    f"key={object_key}",
                    "-F",
                    f"file=@{source};type=application/octet-stream",
                    upload_url,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        match = re.search(r"\nKODO_HTTP_STATUS=(\d{3})\s*$", result.stdout or "")
        if not match:
            detail = (result.stderr or result.stdout or "无响应").strip()[:800]
            raise RuntimeError(f"上传失败：curl 未返回 HTTP 状态（{detail}）")
        status_code = int(match.group(1))
        response_body = (result.stdout or "")[: -len(match.group(0))]
        if status_code in {409, 614}:
            return {"already_exists": True, "status": status_code}
        if status_code < 200 or status_code >= 300:
            body = response_body[:800].replace("\n", " ")
            raise RuntimeError(f"上传失败 HTTP {status_code}：{body}")
        try:
            return json.loads(response_body or "{}")
        except ValueError:
            return {"response": response_body[:800]}

    with source.open("rb") as stream:
        response = requests.post(
            upload_url,
            data={"token": token, "key": object_key},
            files={"file": (source.name, stream, "application/octet-stream")},
            headers={"Expect": "", "Connection": "close"},
            timeout=(15, 3600),
        )
    # insertOnly + 内容寻址键：如果本地回执丢失，重传同 SHA 对象可能收到“已存在”。
    # 这是上传请求的冲突响应，不是远端读取；可安全视为该内容对象已经保存。
    if response.status_code in {409, 614}:
        return {"already_exists": True, "status": response.status_code}
    if response.status_code < 200 or response.status_code >= 300:
        body = response.text[:800].replace("\n", " ")
        raise RuntimeError(f"上传失败 HTTP {response.status_code}: {body}")
    try:
        payload = response.json()
    except ValueError:
        payload = {"response": response.text[:800]}
    return payload


def _run_main() -> int:
    load_simple_env(ROOT / ".env")

    try:
        config = resolve_runtime_config()
    except Exception as exc:
        print(f"Kodo 冷备配置读取失败：{exc}", file=sys.stderr)
        return 2

    if not config:
        print("Kodo 冷备尚未配置或未启用；跳过本次上传。")
        return 0
    if not bool(config.get("enabled")):
        print("Kodo 冷备已配置但当前停用；跳过本次上传。")
        return 0

    backup_dir = Path(
        os.getenv("BACKUP_DIR")
        or (
            Path(os.getenv("PERSIST_ROOT", str(ROOT))) / "backups"
            if os.getenv("PERSIST_ROOT")
            else ROOT / "backups"
        )
    ).expanduser()
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 与 R2 共用同一把锁，避免两个云端备份任务同时生成/覆盖同一时间点的本地全量归档。
    cloud_lock = (backup_dir / ".cloud-backup.lock").open("w")
    try:
        fcntl.flock(cloud_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        cloud_lock.close()
        print("已有 R2/Kodo 云端备份任务在运行，本次 Kodo 跳过。")
        return 0

    # 冷备要求“每日全量容灾”：先在本地生成并校验完整恢复点。
    # 这里仍然只读取本地文件；后续对 Kodo 只发 POST 上传请求。
    env = os.environ.copy()
    env["BACKUP_DIR"] = str(backup_dir)
    try:
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
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stdout or "")
        sys.stderr.write(exc.stderr or "")
        print("Kodo 冷备前的本地全量恢复点生成失败。", file=sys.stderr)
        return 2
    sys.stdout.write(result.stdout)

    manifest_path = ""
    for line in result.stdout.splitlines():
        if line.startswith("FULL_BACKUP_MANIFEST="):
            manifest_path = line.split("=", 1)[1].strip()
    if not manifest_path:
        print("完整容灾脚本没有返回 FULL_BACKUP_MANIFEST", file=sys.stderr)
        return 2

    manifest = Path(manifest_path)
    if not manifest.is_file():
        print(f"完整容灾 manifest 不存在：{manifest}", file=sys.stderr)
        return 2
    values = parse_manifest(manifest)
    timestamp = values.get("timestamp", "")
    if not timestamp:
        print(f"full manifest 无效：{manifest}", file=sys.stderr)
        return 2

    specs = [
        ("base_manifest", "base_manifest_sha256"),
        ("db", "db_sha256"),
        ("data", "data_sha256"),
        ("app", "app_sha256"),
        ("config", "config_sha256"),
        ("docker_image", "docker_image_sha256"),
    ]
    upload_files: list[tuple[str, Path, str]] = []
    for name_key, hash_key in specs:
        name = values.get(name_key, "")
        expected = values.get(hash_key, "")
        if not name:
            continue
        if "/" in name or "\\" in name:
            print(f"manifest 文件名非法：{name}", file=sys.stderr)
            return 2
        source = backup_dir / name
        if not source.is_file():
            print(f"完整恢复点缺少文件：{source}", file=sys.stderr)
            return 2
        actual = sha256_file(source)
        if expected and actual != expected:
            print(f"本地 SHA256 校验失败：{source}", file=sys.stderr)
            return 2
        upload_files.append((name_key, source, actual))

    # full manifest 自身也是内容对象；真正的“每日恢复点提交标记”是最后上传的 snapshot.json。
    upload_files.append(("full_manifest", manifest, sha256_file(manifest)))

    receipt_dir = backup_dir / ".kodo-uploaded"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt = receipt_dir / f"{timestamp}.json"
    if receipt.exists():
        print(f"该全量恢复点已有本地上传成功记录，跳过重复上传：{receipt}")
        return 0

    bucket = str(config["bucket"]).strip()
    upload_url = str(config["upload_url"]).strip().rstrip("/")
    access_key = str(config["access_key"]).strip()
    secret_key = str(config["secret_key"]).strip()
    prefix = str(config.get("prefix") or "ecommerce-workspace/cold").strip().strip("/")
    date_path = f"{timestamp[0:4]}/{timestamp[4:6]}/{timestamp[6:8]}" if len(timestamp) >= 8 else "undated"
    snapshot_root = "/".join(part for part in [prefix, "snapshots", date_path, timestamp] if part)

    print(f"==> Kodo upload-only 每日完整恢复点：{manifest.name}")
    print(f"==> 配置来源：{config.get('source', 'database')}")
    print("==> 规则：内容按 SHA256 去重；只上传，不下载、不取回、不列目录、不做远端校验。")

    known_objects = load_known_objects(receipt_dir)
    objects: list[dict[str, Any]] = []
    uploaded_count = 0
    reused_count = 0

    for role, source, digest in upload_files:
        existing_key = known_objects.get(digest)
        reused = bool(existing_key)
        object_key = existing_key or content_object_key(prefix, digest)
        result_payload: dict[str, Any] = {}
        if not reused:
            result_payload = upload_one(
                upload_url=upload_url,
                access_key=access_key,
                secret_key=secret_key,
                bucket=bucket,
                object_key=object_key,
                source=source,
            )
            known_objects[digest] = object_key
            uploaded_count += 1
            print(f"上传新内容：{source.name} -> {object_key}")
        else:
            reused_count += 1
            print(f"复用已有内容：{source.name} -> {object_key}")

        objects.append(
            {
                "role": role,
                "localFile": source.name,
                "objectKey": object_key,
                "size": source.stat().st_size,
                "sha256": digest,
                "reused": reused,
                "remoteHash": result_payload.get("hash"),
                "alreadyExists": bool(result_payload.get("already_exists")),
            }
        )

    # snapshot.json 是“今天可完整恢复”的提交标记，必须最后上传。
    # 它引用本次所需的全部内容对象，因此即使多数对象复用，今天仍是完整恢复点。
    snapshot_payload = {
        "version": 1,
        "kind": "full",
        "timestamp": timestamp,
        "mode": "upload-only-deduplicated-full",
        "sourceManifest": manifest.name,
        "objects": objects,
        "uploadedCount": uploaded_count,
        "reusedCount": reused_count,
        "createdAt": int(time.time()),
    }
    snapshot_tmp = receipt_dir / f".{timestamp}.snapshot.json"
    snapshot_tmp.write_text(
        json.dumps(snapshot_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    snapshot_key = f"{snapshot_root}/snapshot.json"
    try:
        snapshot_result = upload_one(
            upload_url=upload_url,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            object_key=snapshot_key,
            source=snapshot_tmp,
        )
    finally:
        snapshot_tmp.unlink(missing_ok=True)

    receipt.write_text(
        json.dumps(
            {
                **snapshot_payload,
                "bucket": bucket,
                "upload_url": upload_url,
                "snapshotObjectKey": snapshot_key,
                "snapshotRemoteHash": snapshot_result.get("hash"),
                "snapshotAlreadyExists": bool(snapshot_result.get("already_exists")),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"==> 完成：新增上传 {uploaded_count} 个内容对象，复用 {reused_count} 个；"
        f"恢复点提交标记：{snapshot_key}"
    )
    print(f"==> 本地上传记录：{receipt}")
    return 0


def _backup_dir() -> Path:
    return Path(
        os.getenv("BACKUP_DIR")
        or (
            Path(os.getenv("PERSIST_ROOT", str(ROOT))) / "backups"
            if os.getenv("PERSIST_ROOT")
            else ROOT / "backups"
        )
    ).expanduser()


def _receipt_fingerprint(directory: Path) -> str:
    receipt_dir = directory / ".kodo-uploaded"
    if not receipt_dir.is_dir():
        return ""
    rows = sorted(receipt_dir.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
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
    target = status_dir / "kodo.json"
    tmp = status_dir / ".kodo.json.tmp"
    payload = {
        "target": "kodo",
        "status": status,
        "mode": "full",
        "startedAt": started_at,
        "finishedAt": finished_at,
        "exitCode": exit_code,
    }
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def main() -> int:
    directory = _backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    started = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    before = _receipt_fingerprint(directory)
    _write_job_status(directory, status="running", started_at=started)
    try:
        code = _run_main()
    except Exception:
        finished = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        _write_job_status(directory, status="failed", started_at=started, finished_at=finished, exit_code=2)
        raise
    after = _receipt_fingerprint(directory)
    status = "success" if code == 0 and after != before else "skipped" if code == 0 else "failed"
    finished = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    _write_job_status(directory, status=status, started_at=started, finished_at=finished, exit_code=code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
