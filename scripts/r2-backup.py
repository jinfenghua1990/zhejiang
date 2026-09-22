#!/usr/bin/env python3
"""Cloudflare R2 主备份执行器。

- daily：按模块内容 Hash 去重上传，生成每日快照索引。
- full：上传完整容灾恢复点（应用 / 配置 / 数据库 / data / 可用时 Docker 镜像）。
- auto：每日运行；距离上次 full 达到配置天数时自动执行 full，否则执行 daily。
- R2 是主容灾存储，允许读取；七牛 Kodo 的 upload-only 安全边界不受本脚本影响。
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
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
    if not truthy(os.getenv("R2_BACKUP_ENABLED")):
        return None
    names = {
        "endpoint_url": "R2_BACKUP_ENDPOINT",
        "bucket": "R2_BACKUP_BUCKET",
        "access_key": "R2_BACKUP_ACCESS_KEY",
        "secret_key": "R2_BACKUP_SECRET_KEY",
    }
    missing = [env_name for env_name in names.values() if not (os.getenv(env_name) or "").strip()]
    if missing:
        raise RuntimeError("R2_BACKUP_ENABLED=1，但缺少配置：" + ", ".join(missing))
    return {
        "configured": True,
        "enabled": True,
        "endpoint_url": os.environ[names["endpoint_url"]].strip().rstrip("/"),
        "bucket": os.environ[names["bucket"]].strip(),
        "access_key": os.environ[names["access_key"]].strip(),
        "secret_key": os.environ[names["secret_key"]].strip(),
        "prefix": (os.getenv("R2_BACKUP_PREFIX") or "ecommerce-workspace/backup").strip().strip("/"),
        "fullIntervalDays": int(os.getenv("R2_FULL_INTERVAL_DAYS") or "10"),
        "source": "environment",
    }


def database_runtime_config() -> dict[str, Any] | None:
    backend = ROOT / "backend"
    backend_text = str(backend)
    if backend_text not in sys.path:
        sys.path.insert(0, backend_text)
    from app.db import SessionLocal
    from app.services.r2_backup_service import runtime_config

    db = SessionLocal()
    try:
        config = runtime_config(db)
    finally:
        db.close()
    if not config:
        return None
    return {
        **config,
        "endpoint_url": str(config.get("endpoint_url") or config.get("endpointUrl") or "").strip().rstrip("/"),
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


def q(value: str) -> str:
    return quote(value, safe="-_.~")


def signing_key(secret_key: str, date_stamp: str) -> bytes:
    k_date = hmac.new(("AWS4" + secret_key).encode(), date_stamp.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, b"auto", hashlib.sha256).digest()
    k_service = hmac.new(k_region, b"s3", hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def signed_headers(
    *,
    endpoint_url: str,
    bucket: str,
    object_key: str,
    access_key: str,
    secret_key: str,
    payload_hash: str,
) -> tuple[str, dict[str, str]]:
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    base = urlsplit(endpoint_url)
    if base.scheme != "https" or not base.netloc:
        raise RuntimeError("R2 Endpoint 必须是有效 https:// 地址")
    path_prefix = base.path.rstrip("/")
    canonical_uri = quote(f"{path_prefix}/{bucket}/{object_key}", safe="/-_.~")
    canonical_headers = (
        f"host:{base.netloc}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(["PUT", canonical_uri, "", canonical_headers, signed, payload_hash])
    scope = f"{date_stamp}/auto/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signature = hmac.new(signing_key(secret_key, date_stamp), string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}"
    )
    url = f"{endpoint_url.rstrip('/')}/{q(bucket)}/" + "/".join(q(part) for part in object_key.split("/"))
    return url, {
        "Authorization": authorization,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }


def put_bytes(config: dict[str, Any], object_key: str, payload: bytes, content_type: str) -> None:
    payload_hash = hashlib.sha256(payload).hexdigest()
    url, headers = signed_headers(
        endpoint_url=str(config["endpoint_url"]),
        bucket=str(config["bucket"]),
        object_key=object_key,
        access_key=str(config["access_key"]),
        secret_key=str(config["secret_key"]),
        payload_hash=payload_hash,
    )
    headers["Content-Type"] = content_type
    response = requests.put(url, data=payload, headers=headers, timeout=(15, 300))
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"R2 上传失败 HTTP {response.status_code}: {response.text[:800].replace(chr(10), ' ')}")


def put_file(config: dict[str, Any], object_key: str, source: Path) -> None:
    payload_hash = sha256_file(source)
    url, headers = signed_headers(
        endpoint_url=str(config["endpoint_url"]),
        bucket=str(config["bucket"]),
        object_key=object_key,
        access_key=str(config["access_key"]),
        secret_key=str(config["secret_key"]),
        payload_hash=payload_hash,
    )
    headers["Content-Type"] = "application/octet-stream"
    with source.open("rb") as stream:
        response = requests.put(url, data=stream, headers=headers, timeout=(15, 7200))
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"R2 上传失败 HTTP {response.status_code}: {response.text[:800].replace(chr(10), ' ')}")


def backup_dir() -> Path:
    root = Path(os.getenv("PERSIST_ROOT") or str(ROOT)).expanduser()
    return Path(os.getenv("BACKUP_DIR") or (root / "backups")).expanduser()


def prepare_full_snapshot(*, include_docker: bool) -> tuple[Path, dict[str, str]]:
    env = os.environ.copy()
    env["FULL_BACKUP_INCLUDE_DOCKER_IMAGE"] = "auto" if include_docker else "0"
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


def state_path(directory: Path) -> Path:
    return directory / ".r2-state.json"


def load_state(directory: Path) -> dict[str, Any]:
    path = state_path(directory)
    if not path.is_file():
        return {"modules": {}, "last_full_at": 0, "last_full_timestamp": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"modules": {}, "last_full_at": 0}
    except Exception:
        return {"modules": {}, "last_full_at": 0}


def save_state(directory: Path, state: dict[str, Any]) -> None:
    path = state_path(directory)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def component_paths(directory: Path, values: dict[str, str]) -> list[tuple[str, Path, str]]:
    specs = [
        ("db", "db", "db_sha256"),
        ("data", "data", "data_sha256"),
        ("app", "app", "app_sha256"),
        ("config", "config", "config_sha256"),
        ("docker_image", "docker_image", "docker_image_sha256"),
    ]
    result: list[tuple[str, Path, str]] = []
    for module, name_key, hash_key in specs:
        name = values.get(name_key, "")
        expected = values.get(hash_key, "")
        if not name:
            continue
        if "/" in name or "\\" in name:
            raise RuntimeError(f"manifest 文件名非法：{name}")
        source = directory / name
        if not source.is_file():
            raise RuntimeError(f"恢复点缺少文件：{source}")
        actual = sha256_file(source)
        if expected and actual != expected:
            raise RuntimeError(f"本地 SHA256 校验失败：{source.name}")
        result.append((module, source, actual))
    return result


def date_path(timestamp: str) -> str:
    if len(timestamp) >= 8:
        return f"{timestamp[:4]}/{timestamp[4:6]}/{timestamp[6:8]}"
    return "undated"


def run_daily(config: dict[str, Any], directory: Path, state: dict[str, Any]) -> dict[str, Any]:
    manifest, values = prepare_full_snapshot(include_docker=False)
    timestamp = values.get("timestamp", "")
    if not timestamp:
        raise RuntimeError("完整容灾 manifest 缺少 timestamp")
    prefix = str(config.get("prefix") or "ecommerce-workspace/backup").strip().strip("/")
    modules_state = state.setdefault("modules", {})
    refs: dict[str, Any] = {}

    base_name = values.get("base_manifest", "")
    base_hash = values.get("base_manifest_sha256", "")
    if base_name:
        if "/" in base_name or "\\" in base_name:
            raise RuntimeError(f"base manifest 文件名非法：{base_name}")
        base_source = directory / base_name
        if not base_source.is_file() or (base_hash and sha256_file(base_source) != base_hash):
            raise RuntimeError(f"base manifest 本地校验失败：{base_source}")
        base_digest = base_hash or sha256_file(base_source)
        previous = modules_state.get("base_manifest") if isinstance(modules_state, dict) else None
        if isinstance(previous, dict) and previous.get("sha256") == base_digest and previous.get("object_key"):
            base_object_key = str(previous["object_key"])
            print(f"模块未变化，跳过上传：base_manifest -> {base_object_key}")
        else:
            base_object_key = f"{prefix}/modules/base_manifest/{base_digest}/{base_source.name}"
            print(f"上传变化模块：base_manifest -> {base_object_key}")
            put_file(config, base_object_key, base_source)
            modules_state["base_manifest"] = {
                "sha256": base_digest,
                "object_key": base_object_key,
                "name": base_source.name,
            }
        refs["base_manifest"] = {
            "sha256": base_digest,
            "objectKey": base_object_key,
            "name": base_source.name,
        }

    for module, source, digest in component_paths(directory, values):
        if module == "docker_image":
            continue
        previous = modules_state.get(module) if isinstance(modules_state, dict) else None
        if isinstance(previous, dict) and previous.get("sha256") == digest and previous.get("object_key"):
            object_key = str(previous["object_key"])
            print(f"模块未变化，跳过上传：{module} -> {object_key}")
        else:
            object_key = f"{prefix}/modules/{module}/{digest}/{source.name}"
            print(f"上传变化模块：{module} -> {object_key}")
            put_file(config, object_key, source)
            modules_state[module] = {"sha256": digest, "object_key": object_key, "name": source.name}
        refs[module] = {"sha256": digest, "objectKey": object_key, "name": source.name}

    snapshot = {
        "version": 1,
        "kind": "daily",
        "timestamp": timestamp,
        "gitSha": values.get("git_sha", ""),
        "configEncryption": values.get("config_encryption", ""),
        "configKeyFingerprint": values.get("config_key_fingerprint", ""),
        "configKeyExternalRequired": values.get("config_key_external_required", "") == "1",
        "modules": refs,
        "sourceManifest": manifest.name,
    }
    snapshot_key = f"{prefix}/daily/{date_path(timestamp)}/{timestamp}.json"
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2).encode()
    put_bytes(config, snapshot_key, payload, "application/json")
    put_bytes(config, f"{prefix}/latest-daily.json", payload, "application/json")
    snapshot["snapshotObjectKey"] = snapshot_key
    return snapshot


def run_full(config: dict[str, Any], directory: Path, state: dict[str, Any]) -> dict[str, Any]:
    manifest, values = prepare_full_snapshot(include_docker=True)
    timestamp = values.get("timestamp", "")
    if not timestamp:
        raise RuntimeError("完整容灾 manifest 缺少 timestamp")
    prefix = str(config.get("prefix") or "ecommerce-workspace/backup").strip().strip("/")
    root_key = f"{prefix}/full/{date_path(timestamp)}/{timestamp}"
    refs: dict[str, Any] = {}

    for module, source, digest in component_paths(directory, values):
        object_key = f"{root_key}/{source.name}"
        print(f"上传全量模块：{module} -> {object_key}")
        put_file(config, object_key, source)
        refs[module] = {"sha256": digest, "objectKey": object_key, "name": source.name}

    manifest_key = f"{root_key}/{manifest.name}"
    put_file(config, manifest_key, manifest)
    full_index = {
        "version": 1,
        "kind": "full",
        "timestamp": timestamp,
        "gitSha": values.get("git_sha", ""),
        "configEncryption": values.get("config_encryption", ""),
        "configKeyFingerprint": values.get("config_key_fingerprint", ""),
        "configKeyExternalRequired": values.get("config_key_external_required", "") == "1",
        "manifest": {"name": manifest.name, "objectKey": manifest_key, "sha256": sha256_file(manifest)},
        "modules": refs,
    }
    payload = json.dumps(full_index, ensure_ascii=False, indent=2).encode()
    index_key = f"{root_key}/snapshot.json"
    put_bytes(config, index_key, payload, "application/json")
    put_bytes(config, f"{prefix}/latest-full.json", payload, "application/json")
    full_index["snapshotObjectKey"] = index_key
    state["last_full_at"] = int(dt.datetime.now(dt.timezone.utc).timestamp())
    state["last_full_timestamp"] = timestamp
    return full_index


def write_receipt(directory: Path, snapshot: dict[str, Any]) -> None:
    receipt_dir = directory / ".r2-uploaded"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    timestamp = str(snapshot.get("timestamp") or "unknown")
    kind = str(snapshot.get("kind") or "backup")
    path = receipt_dir / f"{timestamp}-{kind}.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["auto", "daily", "full"], default="auto")
    args = parser.parse_args()

    load_simple_env(ROOT / ".env")
    directory = backup_dir()
    directory.mkdir(parents=True, exist_ok=True)

    # R2 与 Kodo 共用云端备份锁，避免同时读写同一恢复点文件。
    lock_path = directory / ".cloud-backup.lock"
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("已有 R2/Kodo 云端备份任务在运行，本次 R2 跳过。")
            return 0

        try:
            config = resolve_runtime_config()
        except Exception as exc:
            print(f"R2 配置读取失败：{exc}", file=sys.stderr)
            return 2
        if not config:
            print("R2 尚未配置或环境未启用；跳过本次备份。")
            return 0
        if not bool(config.get("enabled")):
            print("R2 已配置但当前停用；跳过本次备份。")
            return 0

        state = load_state(directory)
        mode = args.mode
        if mode == "auto":
            interval_days = max(1, int(config.get("fullIntervalDays") or 10))
            last_full = int(state.get("last_full_at") or 0)
            due = last_full <= 0 or (dt.datetime.now(dt.timezone.utc).timestamp() - last_full) >= interval_days * 86400
            mode = "full" if due else "daily"
            print(f"R2 自动策略：本次执行 {mode}（全量间隔 {interval_days} 天）")

        try:
            snapshot = run_full(config, directory, state) if mode == "full" else run_daily(config, directory, state)
            save_state(directory, state)
            write_receipt(directory, snapshot)
        except subprocess.CalledProcessError as exc:
            sys.stderr.write(exc.stdout or "")
            sys.stderr.write(exc.stderr or "")
            print(f"本地恢复点生成失败：{exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"R2 备份失败：{exc}", file=sys.stderr)
            return 2

        print(f"==> R2 {mode} 备份完成：{snapshot.get('timestamp')}")
        return 0


def _receipt_fingerprint(directory: Path) -> str:
    receipt_dir = directory / ".r2-uploaded"
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
    target = status_dir / "r2.json"
    tmp = status_dir / ".r2.json.tmp"
    payload = {
        "target": "r2",
        "status": status,
        "mode": "backup",
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
