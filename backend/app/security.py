import secrets
import string
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def generate_session_token() -> str:
    return secrets.token_hex(32)


def session_expiry(max_age_seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=max_age_seconds)


_ALNUM = string.ascii_letters + string.digits


def generate_secret(kind: str) -> str:
    """Secret kinds of the Over-App-Vertrag (rule 7)."""
    if kind == "password":
        return "".join(secrets.choice(_ALNUM) for _ in range(24))
    if kind == "token":
        return secrets.token_hex(32)
    if kind == "fernet":
        return Fernet.generate_key().decode()
    raise ValueError(f"unknown secret kind: {kind}")
