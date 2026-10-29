from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Principal, current_user
from app.api.deps import get_arq
from app.core.config import get_settings
from app.core.errors import ConflictError, DomainError, UnauthorizedError, UnprocessableError
from app.core.security import create_token
from app.db.models import AuditLog, User
from app.db.session import get_session
from app.services.users import DEMO_USERS, EmailTaken, UserError, authenticate, create_user, normalize_email

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILED_LOGINS = 10
LOCKOUT_S = 900
MAX_SIGNUPS_PER_HOUR = 5


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    email: str
    name: str
    role: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    user: UserOut


class AuthConfig(BaseModel):
    auth_enabled: bool
    signup_enabled: bool
    signup_domains: list[str]
    demo_accounts: list[dict]


def _issue(user: User) -> TokenOut:
    token, exp = create_token(user.email, user.name, user.role)
    return TokenOut(access_token=token, expires_at=exp.isoformat(), user=UserOut(email=user.email, name=user.name, role=user.role))


@router.get("/config", response_model=AuthConfig)
async def auth_config() -> AuthConfig:
    """Public: lets the UI decide whether to show sign-in, sign-up and the demo accounts."""
    s = get_settings()
    demo = [{"email": e, "role": r, "password": p} for e, _, r, p in DEMO_USERS] if s.auth_enabled and s.demo_users else []
    return AuthConfig(auth_enabled=s.auth_enabled, signup_enabled=s.auth_enabled and s.allow_signup,
                      signup_domains=s.signup_allowed_domains, demo_accounts=demo)


@router.post("/login", response_model=TokenOut)
async def login(req: LoginRequest, session: AsyncSession = Depends(get_session), redis=Depends(get_arq)) -> TokenOut:
    # Brute-force protection shared by all API replicas: 10 failures per account lock it for 15 minutes.
    key = f"login_failures:{normalize_email(req.email)}"
    if int(await redis.get(key) or 0) >= MAX_FAILED_LOGINS:
        raise DomainError("Too many failed sign-in attempts. Try again in 15 minutes.", title="Too many attempts",
                          status_code=429)
    user = await authenticate(session, req.email, req.password)
    if user is None:
        await redis.incr(key)
        await redis.expire(key, LOCKOUT_S)
        raise UnauthorizedError("Wrong email or password.")
    await redis.delete(key)
    session.add(AuditLog(actor=user.email, action="auth.login", entity="user", entity_id=user.id, detail={}))
    await session.commit()
    return _issue(user)


@router.post("/signup", response_model=TokenOut, status_code=201)
async def signup(
    req: SignupRequest, request: Request, session: AsyncSession = Depends(get_session), redis=Depends(get_arq)
) -> TokenOut:
    """Self-service sign-up. Always creates an **analyst**; approver and admin rights are granted by an admin."""
    s = get_settings()
    if not (s.auth_enabled and s.allow_signup):
        raise DomainError("Self-service sign-up is disabled. Ask an administrator for an account.",
                          title="Sign-up disabled", status_code=403)
    client = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    key = f"signups:{client}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 3600)
    if count > MAX_SIGNUPS_PER_HOUR:
        raise DomainError("Too many sign-ups from this address. Try again later.", title="Too many attempts", status_code=429)
    try:
        user = await create_user(session, req.email, req.name, "analyst", req.password, s.signup_allowed_domains)
    except EmailTaken as e:
        raise ConflictError(str(e)) from e
    except UserError as e:
        raise UnprocessableError(str(e)) from e
    session.add(AuditLog(actor=user.email, action="auth.signup", entity="user", entity_id=user.id, detail={"role": user.role}))
    await session.commit()
    return _issue(user)


@router.get("/me", response_model=UserOut)
async def me(user: Principal = Depends(current_user)) -> UserOut:
    return UserOut(email=user.email, name=user.name, role=user.role)
