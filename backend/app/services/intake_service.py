"""Disruption interpretation: LLM structured output first, deterministic rule parser as fallback."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.errors import UnprocessableError
from app.core.logging import get_logger
from app.domain.models import DisruptionSpec
from app.llm.service import LLMCallError, LLMService, LLMUnavailable
from app.services.reference import ReferenceData
from app.services.rule_parser import parse_rules
from app.services.spec_builder import RawIntake, build_spec

log = get_logger(__name__)
MAX_QUERY_CHARS = 2000


@dataclass
class Interpretation:
    spec: DisruptionSpec
    source: str  # llm | rules
    warnings: list[str] = field(default_factory=list)
    model: str | None = None


def _validation_message(e: ValidationError) -> str:
    for err in e.errors():
        msg = str(err.get("msg", ""))
        if "no scope" in msg:
            return msg.replace("Value error, ", "")
    return "The interpretation was not valid: " + "; ".join(str(err.get("msg")) for err in e.errors()[:3])


def _cross_check(spec: DisruptionSpec, text: str, ref: ReferenceData) -> tuple[DisruptionSpec, str] | None:
    """Deterministic sanity check of the LLM interpretation against the rule parser.

    Smaller fallback models occasionally drop or mis-assign places. If the LLM found no geography at all while the
    rule parser did, the rule interpretation is used (the analyst still reviews it before running).
    """
    try:
        rules = build_spec(parse_rules(text, ref.gazetteer, ref.resolver.vocabulary()), text, ref.gazetteer,
                           ref.as_of, ref.params, "rules")
    except ValidationError:
        return None
    if spec.has_geo() or spec.seller_ids or not rules.has_geo():
        return None
    merged = rules.model_copy(update={"category_terms": spec.category_terms or rules.category_terms})
    return merged, (
        "The AI interpretation missed the location named in the text; used the deterministic parser's "
        "geography instead (please review)."
    )


async def interpret(
    text: str,
    ref: ReferenceData,
    llm: LLMService | None,
    *,
    use_llm: bool = True,
    run_id: str | None = None,
) -> Interpretation:
    text = (text or "").strip()
    if len(text) < 5:
        raise UnprocessableError("Describe the disruption in at least a few words.")
    if len(text) > MAX_QUERY_CHARS:
        raise UnprocessableError(f"The description is too long (max {MAX_QUERY_CHARS} characters).")

    warnings: list[str] = []
    if use_llm and llm is not None and llm.available:
        try:
            res = await llm.generate(
                agent="intake", role="intake", prompt="intake",
                context={"as_of": ref.as_of.isoformat(sep=" "), "text": text},
                schema=RawIntake, run_id=run_id, max_output_tokens=8192,
            )
            spec = build_spec(res.parsed, text, ref.gazetteer, ref.as_of, ref.params, "llm")
            checked = _cross_check(spec, text, ref)
            if checked is not None:
                rules_spec, reason = checked
                warnings.append(reason)
                log.warning("intake_cross_check_override", reason=reason, model=res.model)
                return Interpretation(spec=rules_spec, source="rules", warnings=warnings, model=res.model)
            return Interpretation(spec=spec, source="llm", warnings=warnings, model=res.model)
        except (LLMUnavailable, LLMCallError) as e:
            warnings.append(f"The AI interpreter was unavailable ({e}); used the deterministic parser instead.")
        except ValidationError as e:
            warnings.append(f"The AI interpretation was rejected ({_validation_message(e)}); used the deterministic parser instead.")
        log.warning("intake_fallback_to_rules", reason=warnings[-1])
    elif use_llm:
        warnings.append("Offline mode: interpreted with the deterministic parser.")

    raw = parse_rules(text, ref.gazetteer, ref.resolver.vocabulary())
    try:
        spec = build_spec(raw, text, ref.gazetteer, ref.as_of, ref.params, "rules")
    except ValidationError as e:
        raise UnprocessableError(_validation_message(e)) from e
    return Interpretation(spec=spec, source="rules", warnings=warnings)
