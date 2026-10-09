"""Crypto helpers: Fernet (AES-128-CBC + HMAC-SHA256) for API keys at rest, SHA-256 for tokens."""

import hashlib

from cryptography.fernet import Fernet, InvalidToken

from config import settings


def _fernet() -> Fernet:
    key = (settings.encryption_key or "").strip()
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


class DecryptionError(RuntimeError):
    """Raised when a stored secret cannot be decrypted (e.g. the key rotated)."""


def decrypt_strict(token: str) -> str:
    """Decrypt a stored secret, raising DecryptionError on a corrupt token.

    Missing secrets (empty token) return "" so callers can distinguish a
    missing secret from a broken one; a rotated/incorrect key raises rather
    than silently returning a blank credential."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError) as e:
        raise DecryptionError("Stored secret could not be decrypted") from e


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()