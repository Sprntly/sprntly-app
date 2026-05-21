"""Encrypt OAuth token JSON at rest (Fernet)."""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class TokenEncryptionError(ValueError):
    pass


def _fernet() -> Fernet:
    key = (settings.token_encryption_key or "").strip()
    if not key:
        raise TokenEncryptionError("TOKEN_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (TypeError, ValueError) as e:
        raise TokenEncryptionError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key") from e


def encrypt_token_json(plain: str) -> str:
    if not plain:
        raise TokenEncryptionError("Cannot encrypt empty token payload")
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_token_json(cipher: str) -> str:
    if not cipher:
        raise TokenEncryptionError("Cannot decrypt empty ciphertext")
    try:
        return _fernet().decrypt(cipher.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise TokenEncryptionError("Token ciphertext is invalid or key mismatch") from e
