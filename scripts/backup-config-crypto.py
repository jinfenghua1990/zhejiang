#!/usr/bin/env python3
"""Encrypt/decrypt the runtime-config archive used by disaster-recovery backups.

The recovery key is intentionally external to the backup payload.  A new Fernet
key can be created for the first backup, but the operator must copy that key to
a separate secure location before relying on cloud disaster recovery.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.write(b"\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise


def load_key(path: Path, *, create: bool = False) -> bytes:
    if not path.is_file():
        if not create:
            raise RuntimeError(f"恢复密钥不存在：{path}")
        try:
            _write_private(path, Fernet.generate_key())
        except FileExistsError:
            # 允许两个首次备份极短时间内竞争；谁先成功创建，后到者复用同一把密钥。
            pass

    if not path.is_file():
        raise RuntimeError(f"恢复密钥创建失败：{path}")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    key = path.read_bytes().strip()
    try:
        Fernet(key)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"恢复密钥格式无效：{path}") from exc
    return key


def key_fingerprint(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def encrypt_file(source: Path, target: Path, key_file: Path, *, create_key: bool = False) -> str:
    if not source.is_file():
        raise RuntimeError(f"待加密配置包不存在：{source}")
    key = load_key(key_file, create=create_key)
    token = Fernet(key).encrypt(source.read_bytes())
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(token)
    os.chmod(tmp, 0o600)
    tmp.replace(target)
    return key_fingerprint(key)


def decrypt_file(source: Path, target: Path, key_file: Path) -> str:
    if not source.is_file():
        raise RuntimeError(f"加密配置包不存在：{source}")
    key = load_key(key_file, create=False)
    try:
        payload = Fernet(key).decrypt(source.read_bytes())
    except InvalidToken as exc:
        raise RuntimeError("恢复密钥不匹配，或配置密文已经损坏") from exc
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(payload)
    os.chmod(tmp, 0o600)
    tmp.replace(target)
    return key_fingerprint(key)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)

    enc = sub.add_parser("encrypt")
    enc.add_argument("--key-file", required=True)
    enc.add_argument("--input", required=True)
    enc.add_argument("--output", required=True)
    enc.add_argument("--create-key", action="store_true")

    dec = sub.add_parser("decrypt")
    dec.add_argument("--key-file", required=True)
    dec.add_argument("--input", required=True)
    dec.add_argument("--output", required=True)

    fp = sub.add_parser("fingerprint")
    fp.add_argument("--key-file", required=True)

    args = parser.parse_args()
    key_file = Path(args.key_file).expanduser().resolve()

    if args.action == "encrypt":
        fingerprint = encrypt_file(
            Path(args.input).expanduser().resolve(),
            Path(args.output).expanduser().resolve(),
            key_file,
            create_key=bool(args.create_key),
        )
    elif args.action == "decrypt":
        fingerprint = decrypt_file(
            Path(args.input).expanduser().resolve(),
            Path(args.output).expanduser().resolve(),
            key_file,
        )
    else:
        fingerprint = key_fingerprint(load_key(key_file, create=False))

    print(f"KEY_FINGERPRINT={fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
