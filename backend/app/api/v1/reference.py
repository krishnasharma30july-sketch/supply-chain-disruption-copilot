from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.policy_config import get_assumptions, get_category_classes, get_policy, get_scoring
from app.db.models import Category, DatasetMeta
from app.db.session import get_session
from app.llm.service import PROMPT_VERSIONS
from app.services.concepts import CLASS_LABELS
from app.services.geo import REGIONS, STATE_NAMES

router = APIRouter(prefix="/reference", tags=["reference"])


@router.get("/meta")
async def meta(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    s = get_settings()
    build = (await session.execute(select(DatasetMeta.value).where(DatasetMeta.key == "build"))).scalar()
    llm = request.app.state.llm
    return {
        "dataset": build,
        "dataset_ready": build is not None,
        "llm": {
            "mode": llm.mode,
            "available": llm.available,
            "provider": s.llm_provider if llm.enabled else "offline",
            "models": {r: s.model_for(r) for r in ("intake", "email", "adjudicator", "summary")} if llm.enabled else {},
            "max_llm_emails": s.max_llm_emails,
            "breaker_reason": llm.breaker_reason,
            "fallback_models": s.llm_fallback_models if llm.enabled else [],
            "exhausted_models": llm.exhausted_models(),
        },
        "versions": {
            "policy": get_policy().version, "scoring": get_scoring().version, "assumptions": get_assumptions().version,
            "category_classes": get_category_classes().version, "prompts": PROMPT_VERSIONS,
        },
        "embed_model": s.embed_model,
    }


@router.get("/assumptions")
async def assumptions() -> dict:
    a = get_assumptions()
    return {"version": a.version, "params": a.params.model_dump(), "items": [i.model_dump() for i in a.items]}


@router.get("/states")
async def states() -> dict:
    return {"states": [{"code": k, "name": v} for k, v in sorted(STATE_NAMES.items())], "regions": REGIONS}


@router.get("/categories")
async def categories(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(select(Category).order_by(Category.name_en))).scalars().all()
    return {
        "classes": CLASS_LABELS,
        "categories": [
            {"name_pt": c.name_pt, "name_en": c.name_en, "product_class": c.product_class, "class_confidence": c.class_confidence}
            for c in rows
        ],
    }
