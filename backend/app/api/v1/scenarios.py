import asyncio

from fastapi import APIRouter, Depends

from app.api.deps import get_llm, get_ref
from app.api.schemas import OptionPreview, ParseRequest, ParseResponse, PreviewRequest, PreviewResponse
from app.core.policy_config import get_scoring
from app.domain.models import DisruptionSpec
from app.llm.service import LLMService
from app.services.analysis import preview
from app.services.intake_service import interpret
from app.services.reference import ReferenceData
from app.services.spec_builder import apply_ambiguities

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


async def _option_previews(spec: DisruptionSpec, ref: ReferenceData) -> list[OptionPreview]:
    out: list[OptionPreview] = []
    scoring = get_scoring()
    for ai, amb in enumerate(spec.ambiguities):
        for oi, opt in enumerate(amb.options):
            if oi == amb.chosen:
                continue
            alt = spec.model_copy(deep=True)
            alt.ambiguities[ai].chosen = oi
            alt = apply_ambiguities(alt)
            counts = await asyncio.to_thread(preview, alt, ref, scoring)
            out.append(OptionPreview(ambiguity_index=ai, option_index=oi, label=opt.label, preview=counts))
    return out


@router.post("/parse", response_model=ParseResponse)
async def parse_scenario(
    req: ParseRequest, ref: ReferenceData = Depends(get_ref), llm: LLMService = Depends(get_llm)
) -> ParseResponse:
    """Interpret a free-text disruption (LLM, or rules offline) and preview its impact without creating a run."""
    result = await interpret(req.query, ref, llm, use_llm=req.use_llm)
    counts = await asyncio.to_thread(preview, result.spec, ref, get_scoring())
    return ParseResponse(
        spec=result.spec, source=result.source, model=result.model, warnings=result.warnings + counts.warnings,
        preview=counts, option_previews=await _option_previews(result.spec, ref),
    )


@router.post("/preview", response_model=PreviewResponse)
async def preview_scenario(req: PreviewRequest, ref: ReferenceData = Depends(get_ref)) -> PreviewResponse:
    """Re-compute preview counts for an edited interpretation."""
    spec = apply_ambiguities(req.spec)
    counts = await asyncio.to_thread(preview, spec, ref, get_scoring())
    return PreviewResponse(spec=spec, preview=counts)
