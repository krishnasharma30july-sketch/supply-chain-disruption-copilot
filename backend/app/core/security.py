"""Password hashing (scrypt, stdlib) and JWT access tokens (HS256)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import jwt

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

ROLES = ("analyst", "approver", "admin")
ROLE_RANK = {r: i for i, r in enumerate(ROLES)}
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT, dklen=32)
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


@lru_cache
def jwt_secret() -> str:
    """JWT_SECRET from the environment, else a random secret persisted in DATA_DIR (shared by API replicas
    on the same volume, stable across restarts)."""
    s = get_settings()
    if s.jwt_secret:
        return s.jwt_secret
    path = Path(s.data_dir) / ".jwt_secret"
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(48)
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
        log.warning("jwt_secret_generated", path=str(path))
        return value
    except OSError:
        log.warning("jwt_secret_ephemeral")  # tokens will not survive a restart
        return secrets.token_urlsafe(48)


def create_token(sub: str, name: str, role: str) -> tuple[str, datetime]:
    exp = datetime.now(timezone.utc) + timedelta(minutes=get_settings().jwt_ttl_minutes)
    token = jwt.encode({"sub": sub, "name": name, "role": role, "exp": exp}, jwt_secret(), algorithm=ALGORITHM)
    return token, exp


def decode_token(token: str) -> dict:
    """Raises jwt.PyJWTError when the token is invalid or expired."""
    return jwt.decode(token, jwt_secret(), algorithms=[ALGORITHM], options={"require": ["sub", "role", "exp"]})
