"""Authentication and role checks as FastAPI dependencies.

Credentials, in order: `Authorization: Bearer <jwt>`, `X-API-Key` (service credential, admin role) and, for GET
requests only, an `access_token` query parameter (EventSource and download links cannot send headers).
The token proves identity; the account's current state in the database decides access, so deactivating a user
or changing their role takes effect on their next request.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import ROLE_RANK, decode_token
from app.db.session import get_session


@dataclass(frozen=True)
class Principal:
    email: str
    name: str
    role: str
    from_token: bool = False  # True when the identity comes from a JWT (must match an active account)

    def has(self, role: str) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK[role]


LOCAL_PRINCIPAL = Principal(email="local", name="Local user", role="admin")  # AUTH_ENABLED=false


def principal_from_request(request: Request) -> Principal:
    """Identity from the request credentials (no database access)."""
    settings = get_settings()
    if not settings.auth_enabled:
        return LOCAL_PRINCIPAL
    supplied_key = request.headers.get("x-api-key")
    if settings.api_key and supplied_key and hmac.compare_digest(supplied_key, settings.api_key):
        return Principal(email="api-key", name="Service", role="admin")
    token = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    elif request.method == "GET":
        token = request.query_params.get("access_token")
    if not token:
        raise UnauthorizedError("Sign in required.")
    try:
        claims = decode_token(token)
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedError("Session expired. Sign in again.") from e
    except jwt.PyJWTError as e:
        raise UnauthorizedError("Invalid token.") from e
    if claims["role"] not in ROLE_RANK:
        raise UnauthorizedError("Invalid token.")
    return Principal(email=claims["sub"], name=claims.get("name") or claims["sub"], role=claims["role"], from_token=True)


async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> Principal:
    from app.services.users import get_active_user

    principal = principal_from_request(request)
    if not principal.from_token:
        return principal
    user = await get_active_user(session, principal.email)
    if user is None:
        raise UnauthorizedError("This account is no longer active.")
    return Principal(email=user.email, name=user.name, role=user.role, from_token=True)


def require_role(role: str):
    def dep(user: Principal = Depends(current_user)) -> Principal:
        if not user.has(role):
            raise ForbiddenError(f"This action requires the {role} role (you are signed in as {user.role}).")
        return user

    return dep


require_approver = require_role("approver")
require_admin = require_role("admin")
