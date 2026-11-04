"""Tone and empathy review of AI-written emails.

The review is advisory: it rates warmth and clarity (1 to 5) for emails that already passed the fact check. It can
trigger one rewrite, but it never changes whether an email may be sent.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from app.core.logging import get_logger
from app.llm.outputs import ToneBatchOut
from app.llm.service import LLMCallError, LLMService, LLMUnavailable

log = get_logger(__name__)


@dataclass(frozen=True)
class ToneItem:
    ref: str
    locale: str
    subject: str
    body: str  # placeholder form ([[TRACK_LINK]] ...)


@dataclass(frozen=True)
class ToneVerdict:
    warmth: int
    clarity: int
    notes: str

    @property
    def score(self) -> int:
        return self.warmth + self.clarity

    def is_weak(self, min_score: int) -> bool:
        return min(self.warmth, self.clarity) < min_score

    def as_dict(self) -> dict:
        return {"warmth": self.warmth, "clarity": self.clarity, "notes": self.notes}


def _clamp(value: int) -> int:
    return max(1, min(5, int(value)))


async def review_tone(
    llm: LLMService, items: list[ToneItem], *, guide: str, batch_size: int, run_id: str | None = None
) -> dict[str, ToneVerdict]:
    """Rate emails in batches. Emails whose batch could not be reviewed are simply missing from the result."""
    out: dict[str, ToneVerdict] = {}
    batches = [items[i : i + batch_size] for i in range(0, len(items), batch_size)]

    async def one(batch: list[ToneItem]) -> None:
        payload = [{"customer_ref": i.ref, "locale": i.locale, "subject": i.subject, "body": i.body} for i in batch]
        try:
            res = await llm.generate(
                agent="qa_critic", role="email", prompt="tone",
                context={"tone_guide": guide, "emails_json": json.dumps(payload, ensure_ascii=False, indent=1)},
                schema=ToneBatchOut, run_id=run_id, max_output_tokens=4096,
            )
        except (LLMUnavailable, LLMCallError) as e:
            log.warning("tone_review_batch_failed", error=str(e)[:200], size=len(batch))
            return
        refs = {i.ref for i in batch}
        for r in res.parsed.reviews:
            ref = r.customer_ref.strip()
            if ref in refs and ref not in out:
                out[ref] = ToneVerdict(_clamp(r.warmth), _clamp(r.clarity), r.notes.strip()[:300])

    await asyncio.gather(*(one(b) for b in batches))
    return out
