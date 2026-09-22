#!/usr/bin/env python3
"""从 Cloudflare R2 准备最新完整容灾恢复点。

安全边界：
- 只下载到独立 staging 目录，不覆盖生产数据库、data/、代码或 .env。
- 下载后逐文件 SHA256 校验，并调用 restore-check.sh 对数据库做临时库恢复演练。
- 真正覆盖生产环境必须另行人工确认，不由本脚本自动执行。
"""

from __future__ import annotations

import argparse
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

ROOT = Path(__file__).resolve().parents[1]
EMPTY_HASH = hashlib.sha256(b"").hexdigest()


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
    if any(not (os.getenv(name) or "").strip() for name in names.values()):
        return None
    return {
        "enabled": True,
        "endpoint_url": os.environ[names["endpoint_url"]].strip().rstrip("/"),
        "bucket": os.environ[names["bucket"]].strip(),
        "access_key": os.environ[names["access_key"]].strip(),
        "secret_key": os.environ[names["secret_key"]].strip(),
        "prefix": (os.getenv("R2_BACKUP_PREFIX") or "ecommerce-workspace/backup").strip().strip("/"),
    }


def database_runtime_config() -> dict[str, Any] | None:
    backend = ROOT / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
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
    }


def resolve_config() -> dict[str, Any] | None:
    return env_runtime_config() or database_runtime_config()


def signing_key(secret_key: str, date_stamp: str) -> bytes:
    k_date = hmac.new(("AWS4" + secret_key).encode(), date_stamp.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, b"auto", hashlib.sha256).digest()
    k_service = hmac.new(k_region, b"s3", hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def signed_get(config: dict[str, Any], object_key: str) -> requests.Response:
    endpoint_url = str(config["endpoint_url"]).rstrip("/")
    base = urlsplit(endpoint_url)
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    path_prefix = base.path.rstrip("/")
    canonical_uri = quote(f"{path_prefix}/{config['bucket']}/{object_key}", safe="/-_.~")
    canonical_headers = (
        f"host:{base.netloc}\n"
        f"x-amz-content-sha256:{EMPTY_HASH}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(["GET", canonical_uri, "", canonical_headers, signed, EMPTY_HASH])
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
        signing_key(str(config["secret_key"]), date_stamp),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={config['access_key']}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}"
    )
    url = f"{endpoint_url}/" + "/".join(
        quote(part, safe="-_.~") for part in (str(config["bucket"]) + "/" + object_key).split("/")
    )
    return requests.get(
        url,
        headers={
            "Authorization": authorization,
            "x-amz-content-sha256": EMPTY_HASH,
            "x-amz-date": amz_date,
        },
        timeout=(15, 7200),
        stream=True,
    )


def get_json(config: dict[str, Any], object_key: str) -> dict[str, Any]:
    response = signed_get(config, object_key)
    try:
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"R2 读取失败 HTTP {response.status_code}: {response.text[:800]}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("R2 快照索引格式无效")
        return payload
    finally:
        response.close()


def download(config: dict[str, Any], object_key: str, target: Path, expected_hash: str) -> None:
    response = signed_get(config, object_key)
    tmp = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    try:
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"R2 下载失败 HTTP {response.status_code}: {response.text[:800]}")
        with tmp.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                digest.update(chunk)
                stream.write(chunk)
    finally:
        response.close()
    actual = digest.hexdigest()
    if expected_hash and actual != expected_hash:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"SHA256 校验失败：{target.name}")
    tmp.replace(target)


def safe_name(name: str) -> str:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise RuntimeError(f"恢复文件名非法：{name!r}")
    return name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-restore-check", action="store_true")
    parser.add_argument(
        "--snapshot-key",
        default="",
        help="指定 R2 full/.../snapshot.json；留空则使用 latest-full.json",
    )
    args = parser.parse_args()

    load_simple_env(ROOT / ".env")
    config = resolve_config()
    if not config or not config.get("enabled"):
        print("R2 尚未配置或当前未启用。", file=sys.stderr)
        return 2

    prefix = str(config.get("prefix") or "ecommerce-workspace/backup").strip().strip("/")
    snapshot_key = str(args.snapshot_key or "").strip()
    if snapshot_key:
        expected_root = f"{prefix}/full/"
        if (
            snapshot_key.startswith("/")
            or "\\" in snapshot_key
            or ".." in snapshot_key.split("/")
            or not snapshot_key.startswith(expected_root)
            or not snapshot_key.endswith("/snapshot.json")
        ):
            print("R2 恢复点路径无效或不属于当前备份前缀", file=sys.stderr)
            return 2
    index_key = snapshot_key or f"{prefix}/latest-full.json"
    try:
        index = get_json(config, index_key)
    except Exception as exc:
        label = "指定全量恢复点" if snapshot_key else "最新全量恢复点"
        print(f"无法读取 R2 {label}：{exc}", file=sys.stderr)
        return 2

    timestamp = str(index.get("timestamp") or "")
    if not timestamp:
        print("R2 latest-full.json 缺少 timestamp", file=sys.stderr)
        return 2

    base_backup = Path(
        os.getenv("BACKUP_DIR")
        or (Path(os.getenv("PERSIST_ROOT") or str(ROOT)).expanduser() / "backups")
    ).expanduser()
    staging_root = Path(os.getenv("RESTORE_STAGING_DIR") or (base_backup / "r2-restore")).expanduser()
    target_dir = staging_root / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        manifest_meta = index.get("manifest") or {}
        manifest_name = safe_name(str(manifest_meta.get("name") or f"full_{timestamp}.manifest"))
        download(
            config,
            str(manifest_meta.get("objectKey") or ""),
            target_dir / manifest_name,
            str(manifest_meta.get("sha256") or ""),
        )

        modules = index.get("modules") or {}
        if not isinstance(modules, dict):
            raise RuntimeError("恢复点 modules 无效")
        downloaded: dict[str, dict[str, str]] = {}
        for module, meta in modules.items():
            if not isinstance(meta, dict):
                continue
            name = safe_name(str(meta.get("name") or ""))
            object_key = str(meta.get("objectKey") or "")
            expected = str(meta.get("sha256") or "")
            if not object_key or not expected:
                raise RuntimeError(f"恢复模块信息不完整：{module}")
            download(config, object_key, target_dir / name, expected)
            downloaded[str(module)] = {"name": name, "sha256": expected, "objectKey": object_key}

        db = downloaded.get("db")
        if not db:
            raise RuntimeError("完整恢复点缺少数据库模块")
        data = downloaded.get("data")
        base_manifest = downloaded.get("base_manifest")
        if base_manifest:
            local_manifest = target_dir / base_manifest["name"]
        else:
            # 兼容早期 R2 全量快照：当时未单独上传基础 manifest，则按索引重建。
            local_manifest = target_dir / f"backup_{timestamp}.manifest"
            local_manifest.write_text(
                "\n".join(
                    [
                        "version=1",
                        f"timestamp={timestamp}",
                        f"db={db['name']}",
                        f"db_sha256={db['sha256']}",
                        f"data={data['name'] if data else ''}",
                        f"data_sha256={data['sha256'] if data else ''}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

        if not args.skip_restore_check:
            env = os.environ.copy()
            env["BACKUP_DIR"] = str(target_dir)
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "restore-check.sh")],
                cwd=str(ROOT),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            (target_dir / "restore-check.log").write_text(result.stdout + result.stderr, encoding="utf-8")

        ready = {
            "timestamp": timestamp,
            "source": "Cloudflare R2",
            "snapshotObjectKey": snapshot_key,
            "validated": not args.skip_restore_check,
            "directory": str(target_dir),
            "modules": downloaded,
            "configEncryption": str(index.get("configEncryption") or ""),
            "configKeyFingerprint": str(index.get("configKeyFingerprint") or ""),
            "configKeyExternalRequired": bool(index.get("configKeyExternalRequired")),
            "note": "仅准备到 staging；尚未覆盖生产环境。运行配置若已加密，需另行提供独立恢复密钥。",
        }
        (target_dir / "READY.json").write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except subprocess.CalledProcessError as exc:
        (target_dir / "restore-check.log").write_text((exc.stdout or "") + (exc.stderr or ""), encoding="utf-8")
        print("R2 恢复点下载成功，但数据库恢复演练失败。", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"R2 恢复准备失败：{exc}", file=sys.stderr)
        return 2

    print(f"==> R2 完整恢复点已下载并校验：{target_dir}")
    print("==> 未覆盖生产环境；真正恢复需要后续明确确认。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
