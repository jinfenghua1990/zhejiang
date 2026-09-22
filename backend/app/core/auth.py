"""登录鉴权核心：PBKDF2 口令散列 + HMAC-SHA256 签名令牌。

只用标准库实现（无新增依赖，避免镜像构建风险）：
- 口令：pbkdf2_sha256$<iterations>$<salt_hex>$<dk_hex>，常量时间比较
- 令牌：base64url(payload).base64url(hmac_sha256(payload))，payload 含 sub/uid/exp/iat/ver
密钥统一派生自 APP_SECRET_KEY（服务端 .env，禁止进入前端/Git）。
"""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time

from app.config import settings

# OWASP 当前对 PBKDF2-HMAC-SHA256 的建议工作因子为 600,000。
# 迭代次数写在每条 hash 中，因此旧 120,000 次密码仍可验证，并可在成功登录时平滑升级。
_PBKDF2_ITERATIONS = 600_000
_TOKEN_TTL_SECONDS = 12 * 3600  # 12 小时，过期重新登录


# ---------- 口令 ----------

def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(plain: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, iterations, salt_hex, dk_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", plain.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


def password_needs_rehash(stored: str | None) -> bool:
    """旧工作因子在成功登录后升级；格式异常由 verify_password 先行拒绝。"""
    if not stored:
        return True
    try:
        algo, iterations, _salt_hex, _dk_hex = stored.split("$")
        return algo != "pbkdf2_sha256" or int(iterations) < _PBKDF2_ITERATIONS
    except (ValueError, TypeError):
        return True


# ---------- 令牌 ----------

def _signing_key() -> bytes:
    if not settings.APP_SECRET_KEY:
        raise RuntimeError("APP_SECRET_KEY 未配置，拒绝签发令牌")
    return hashlib.sha256(settings.APP_SECRET_KEY.encode()).digest()


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_token(
    *, uid: int, username: str, token_version: int = 0,
    ttl_seconds: int = _TOKEN_TTL_SECONDS,
) -> str:
    payload = {
        "sub": username,
        "uid": uid,
        "ver": token_version,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_seconds,
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    sig = _b64e(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def decode_token(token: str) -> dict | None:
    """校验签名与有效期；通过返回 payload，失败返回 None。"""
    if not token or token.count(".") != 1:
        return None
    body, sig = token.split(".")
    try:
        expected = hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64e(expected), sig):
            return None
        payload = json.loads(_b64d(body))
        if not isinstance(payload, dict):
            return None
        if int(payload.get("exp", 0)) < time.time():
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error, UnicodeDecodeError):
        return None
