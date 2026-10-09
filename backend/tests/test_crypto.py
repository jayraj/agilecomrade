import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet

import crypto


def setup_module() -> None:
    crypto.settings.encryption_key = Fernet.generate_key().decode()


def test_encrypt_decrypt_roundtrip() -> None:
    token = "super-secret-jira-token"
    encrypted = crypto.encrypt(token)
    assert encrypted != token
    assert crypto.decrypt_strict(encrypted) == token


def test_empty_string() -> None:
    assert crypto.encrypt("") == ""
    assert crypto.decrypt_strict("") == ""


def test_decrypt_strict_roundtrip_and_empty() -> None:
    assert crypto.decrypt_strict(crypto.encrypt("abc")) == "abc"
    assert crypto.decrypt_strict("") == ""


def test_decrypt_strict_raises_on_corrupt_token() -> None:
    import pytest

    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_strict("not-a-valid-token")


def test_sha256_hex_is_deterministic() -> None:
    assert crypto.sha256_hex("abc") == crypto.sha256_hex("abc")
    assert crypto.sha256_hex("abc") != crypto.sha256_hex("abd")
