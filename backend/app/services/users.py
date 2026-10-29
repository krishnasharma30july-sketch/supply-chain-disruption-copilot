"""User accounts: creation, sign-up, authentication, administration and demo seeding."""

from __future__ import annotations

import asyncio
import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import ROLES, hash_password, verify_password
from app.db.models import User

# Seeded only when DEMO_USERS=true (the local docker-compose stack sets it); missing ones are added on startup.
DEMO_USERS = [
    ("analyst@example.com", "Ana Analyst", "analyst", "analyst-demo"),
    ("approver@example.com", "Paulo Approver", "approver", "approver-demo"),
    ("admin@example.com", "Alex Admin", "admin", "admin-demo"),
]
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
MIN_PASSWORD = 8


class UserError(ValueError):
    """Invalid user input (shown to the caller)."""


class EmailTaken(UserError):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_new_user(email: str, name: str, password: str, allowed_domains: list[str] | None = None) -> str:
    """Returns the normalized email or raises UserError."""
    email = normalize_email(email)
    if not EMAIL_RE.match(email) or len(email) > 120:
        raise UserError("Enter a valid email address.")
    domain = email.rsplit("@", 1)[1]
    allowed = [d.lower().lstrip("@") for d in (allowed_domains or []) if d.strip()]
    if allowed and domain not in allowed:
        raise UserError(f"Sign-up is limited to {', '.join('@' + d for d in allowed)} addresses.")
    if not name.strip() or len(name.strip()) > 80:
        raise UserError("Enter your name (up to 80 characters).")
    if len(password) < MIN_PASSWORD:
        raise UserError(f"The password must have at least {MIN_PASSWORD} characters.")
    if password.strip().lower() in {email, email.split("@")[0]} or len(set(password)) < 4:
        raise UserError("Choose a less guessable password.")
    return email


async def create_user(session: AsyncSession, email: str, name: str, role: str, password: str,
                      allowed_domains: list[str] | None = None) -> User:
    if role not in ROLES:
        raise UserError(f"role must be one of {', '.join(ROLES)}")
    email = validate_new_user(email, name, password, allowed_domains)
    user = User(id=str(uuid.uuid4()), email=email, name=name.strip(), role=role,
                password_hash=await asyncio.to_thread(hash_password, password), active=True)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise EmailTaken("An account with this email already exists. Sign in instead.") from e
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    user = (await session.execute(select(User).where(User.email == normalize_email(email)))).scalar_one_or_none()
    if user is None or not user.active:
        await asyncio.to_thread(hash_password, password)  # same cost either way: no user-enumeration timing signal
        return None
    ok = await asyncio.to_thread(verify_password, password, user.password_hash)
    return user if ok else None


async def get_active_user(session: AsyncSession, email: str) -> User | None:
    user = (await session.execute(select(User).where(User.email == normalize_email(email)))).scalar_one_or_none()
    return user if user is not None and user.active else None


async def update_user(session: AsyncSession, user: User, *, role: str | None, active: bool | None, actor_email: str) -> User:
    """Admin change of role / active flag. An admin cannot demote or deactivate themselves (no lock-out)."""
    if user.email == actor_email and ((role is not None and role != "admin") or active is False):
        raise UserError("You cannot remove your own admin access or deactivate yourself.")
    if role is not None:
        if role not in ROLES:
            raise UserError(f"role must be one of {', '.join(ROLES)}")
        user.role = role
    if active is not None:
        user.active = active
    await session.commit()
    await session.refresh(user)
    return user


async def seed_demo_users(session: AsyncSession) -> int:
    existing = set((await session.execute(select(User.email))).scalars().all())
    added = 0
    for email, name, role, password in DEMO_USERS:
        if email not in existing:
            await create_user(session, email, name, role, password)
            added += 1
    return added
