from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from app.db.models import DatasetMeta
from app.db.session import get_sessionmaker

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        async with get_sessionmaker()() as s:
            await s.execute(text("SELECT 1"))
            build = (await s.execute(select(DatasetMeta.key).where(DatasetMeta.key == "build"))).scalar()
            checks["database"] = "ok"
            checks["dataset"] = "ok" if build else "not loaded"
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
    try:
        await request.app.state.arq.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {type(e).__name__}"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if healthy else "degraded", "checks": checks}, status_code=200 if healthy else 503)
