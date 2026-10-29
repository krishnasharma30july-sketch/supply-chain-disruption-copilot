from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Principal, require_admin
from app.core.errors import NotFoundError, UnprocessableError
from app.db.models import AuditLog, User
from app.db.session import get_session
from app.services.users import UserError, update_user

router = APIRouter(prefix="/users", tags=["users"])


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    name: str
    role: str
    active: bool
    created_at: datetime


class UserUpdate(BaseModel):
    role: Literal["analyst", "approver", "admin"] | None = None
    active: bool | None = None


@router.get("", response_model=list[AdminUserOut])
async def list_users(session: AsyncSession = Depends(get_session), _admin: Principal = Depends(require_admin)) -> list[User]:
    return list((await session.execute(select(User).order_by(User.created_at, User.email))).scalars().all())


@router.patch("/{user_id}", response_model=AdminUserOut)
async def change_user(
    user_id: str, req: UserUpdate, session: AsyncSession = Depends(get_session), admin: Principal = Depends(require_admin)
) -> User:
    """Grant or remove roles and (de)activate accounts. Takes effect on the user's next request."""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"User {user_id} not found.")
    before = {"role": user.role, "active": user.active}
    try:
        user = await update_user(session, user, role=req.role, active=req.active, actor_email=admin.email)
    except UserError as e:
        raise UnprocessableError(str(e)) from e
    session.add(AuditLog(actor=admin.email, action="user.update", entity="user", entity_id=user.id,
                         detail={"before": before, "after": {"role": user.role, "active": user.active}}))
    await session.commit()
    return user
