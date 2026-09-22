import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import settings

"""integration_credentials 落库加密：Fernet，密钥派生自 APP_SECRET_KEY。"""


def _fernet() -> Fernet:
    if not settings.APP_SECRET_KEY:
        raise RuntimeError("APP_SECRET_KEY 未配置，拒绝加解密凭证")
    digest = hashlib.sha256(settings.APP_SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
