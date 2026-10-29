"""Auth (password hashing, JWT, roles), metrics labels and outbound email construction."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from starlette.requests import Request

from app.api.auth import Principal, current_user, principal_from_request, require_role
from app.core import security
from app.core.config import get_settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.metrics import route_label
from app.services.mailer import build_message, synthetic_address


@pytest.fixture(autouse=True)
def _auth_settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "auth_enabled", True)
    monkeypatch.setattr(s, "jwt_secret", "test-secret-" + "x" * 32)
    monkeypatch.setattr(s, "api_key", "svc-key")
    security.jwt_secret.cache_clear()
    yield
    security.jwt_secret.cache_clear()


def _request(method="GET", headers=None, query=b""):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": method, "headers": raw, "query_string": query, "path": "/api/v1/x"})


def test_password_hash_roundtrip():
    h = security.hash_password("correct horse")
    assert h.startswith("scrypt$") and "correct horse" not in h
    assert security.verify_password("correct horse", h)
    assert not security.verify_password("wrong", h)
    assert not security.verify_password("x", "garbage")
    assert security.hash_password("same") != security.hash_password("same")  # salted


def test_bearer_token_gives_principal():
    token, _ = security.create_token("ana@example.com", "Ana", "analyst")
    user = principal_from_request(_request(headers={"Authorization": f"Bearer {token}"}))
    assert user == Principal(email="ana@example.com", name="Ana", role="analyst", from_token=True)


def test_query_token_only_for_get():
    token, _ = security.create_token("ana@example.com", "Ana", "analyst")
    assert principal_from_request(_request(query=f"access_token={token}".encode())).role == "analyst"
    with pytest.raises(UnauthorizedError):
        principal_from_request(_request(method="POST", query=f"access_token={token}".encode()))


def test_missing_expired_and_forged_tokens_are_rejected():
    with pytest.raises(UnauthorizedError):
        principal_from_request(_request())
    expired = jwt.encode({"sub": "a", "role": "admin", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                         security.jwt_secret(), algorithm="HS256")
    with pytest.raises(UnauthorizedError, match="expired"):
        principal_from_request(_request(headers={"Authorization": f"Bearer {expired}"}))
    forged = jwt.encode({"sub": "a", "role": "admin", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                        "another-secret-" + "y" * 32, algorithm="HS256")
    with pytest.raises(UnauthorizedError):
        principal_from_request(_request(headers={"Authorization": f"Bearer {forged}"}))


def test_api_key_is_a_service_admin():
    assert principal_from_request(_request(headers={"X-API-Key": "svc-key"})).role == "admin"
    with pytest.raises(UnauthorizedError):
        principal_from_request(_request(headers={"X-API-Key": "nope"}))


def test_roles_are_ordered():
    analyst = Principal("a", "A", "analyst")
    approver = Principal("b", "B", "approver")
    dep = require_role("approver")
    with pytest.raises(ForbiddenError):
        dep(analyst)
    assert dep(approver) is approver
    assert Principal("c", "C", "admin").has("approver")


def test_auth_disabled_is_local_admin(monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_enabled", False)
    assert principal_from_request(_request()).role == "admin"


def test_route_label_uses_template_or_collapses_ids():
    assert route_label({"route": SimpleNamespace(path="/api/v1/runs/{run_id}")}) == "/api/v1/runs/{run_id}"
    assert route_label({"path": "/api/v1/runs/0b9d6a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b/orders"}) == "/api/v1/runs/{id}/orders"
    assert route_label({"path": "/x/0123456789abcdef0123456789abcdef"}) == "/x/{id}"


def test_email_message_headers():
    comm = SimpleNamespace(id="11111111-2222-3333-4444-555555555555", run_id="run-1", customer_unique_id="a" * 32,
                           subject="Atualização do seu pedido", body="Olá Ana,\nseu pedido...", locale="pt-BR",
                           version=2, email_to=None)
    msg = build_message(comm, get_settings())
    assert msg["To"] == synthetic_address("a" * 32) and msg["To"].endswith("@customers.example")
    assert msg["Subject"] == "Atualização do seu pedido"
    assert msg["X-Copilot-Communication"].endswith("v2")
    assert "seu pedido" in msg.get_content()


# --- accounts: sign-up validation, live account check, admin lock-out protection -------------------------------


def test_signup_validation():
    from app.services.users import UserError, validate_new_user

    assert validate_new_user(" Ana@Company.com ", "Ana", "s3cure-pass") == "ana@company.com"
    for email, name, pw in [("not-an-email", "Ana", "s3cure-pass"), ("a@b.com", "", "s3cure-pass"),
                            ("a@b.com", "Ana", "short"), ("a@b.com", "Ana", "aaaaaaaa")]:
        with pytest.raises(UserError):
            validate_new_user(email, name, pw)
    with pytest.raises(UserError, match="limited to @company.com"):
        validate_new_user("ana@gmail.com", "Ana", "s3cure-pass", ["company.com"])


@pytest.mark.asyncio
async def test_token_access_follows_the_live_account(monkeypatch):
    import app.services.users as users

    token, _ = security.create_token("ana@example.com", "Ana", "analyst")
    req = _request(headers={"Authorization": f"Bearer {token}"})

    async def promoted(_s, _e):
        return SimpleNamespace(email="ana@example.com", name="Ana", role="approver")

    monkeypatch.setattr(users, "get_active_user", promoted)
    assert (await current_user(req, session=None)).role == "approver"  # role change applies without re-login

    async def gone(_s, _e):
        return None

    monkeypatch.setattr(users, "get_active_user", gone)
    with pytest.raises(UnauthorizedError, match="no longer active"):
        await current_user(req, session=None)


@pytest.mark.asyncio
async def test_admin_cannot_lock_themselves_out():
    from app.services.users import UserError, update_user

    me = SimpleNamespace(email="admin@example.com", role="admin", active=True)
    with pytest.raises(UserError):
        await update_user(None, me, role="analyst", active=None, actor_email="admin@example.com")
    with pytest.raises(UserError):
        await update_user(None, me, role=None, active=False, actor_email="admin@example.com")