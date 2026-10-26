"""API anahtarlarını veritabanında şifreli saklamak için yardımcılar."""
import base64
import hashlib
import json

from cryptography.fernet import Fernet

from . import config


def _fernet() -> Fernet:
    key = hashlib.sha256(("panel-v1:" + config.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt(token: str) -> dict:
    return json.loads(_fernet().decrypt(token.encode()).decode())


def mask(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "••••"
