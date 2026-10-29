"""Reporter agent: deterministic stats and recommendations plus a number-checked executive narrative."""

from __future__ import annotations

import json

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agents.base import AgentContext, RunState
from app.db.models import RunSummary
from app.llm.outputs import NarrativeOut
from app.llm.service import LLMCallError, LLMUnavailable
from app.services.reporting import (
    check_narrative,
    compute_stats,
    narrative_facts,
    recommendations,
    template_narrative,
)


class ReporterAgent:
    name = "reporter"
    label = "Writing the impact summary"

    async def run(self, state: RunState, ctx: AgentContext) -> None:
        assert state.spec is not None
        comms = state.metrics.get("communications", {})
        stats = compute_stats(state.spec, state.impacts, state.decisions, comms, ctx.ref.seller_geo)
        recs = recommendations(stats, state.spec)
        facts = narrative_facts(stats, state.spec)
        headline, narrative = template_narrative(stats, state.spec)
        generator = "template"
        check: dict = {"checked": False, "problems": []}

        if ctx.use_llm and ctx.llm.available and stats["scope"]["orders_delayed"] > 0:
            feedback = ""
            for attempt in range(2):
                try:
                    res = await ctx.llm.generate(
                        agent=self.name, role="summary", prompt="narrative",
                        context={"facts_json": json.dumps(facts, ensure_ascii=False, indent=1), "feedback": feedback},
                        schema=NarrativeOut, run_id=ctx.run_id, max_output_tokens=8192,
                    )
                except (LLMUnavailable, LLMCallError) as e:
                    await ctx.emitter.emit(self.name, "warning", f"Narrative LLM unavailable ({e}); using the template.", level="warning")
                    break
                text = f"{res.parsed.headline}\n{res.parsed.narrative}"
                problems = check_narrative(text, facts)
                check = {"checked": True, "problems": problems, "attempt": attempt + 1}
                if not problems:
                    headline, narrative, generator = res.parsed.headline.strip(), res.parsed.narrative.strip(), "llm"
                    break
                feedback = "; ".join(problems[:6])
                await ctx.emitter.emit(
                    self.name, "warning", f"Narrative used ungrounded numbers ({feedback}); retrying.", level="warning"
                )
            if generator != "llm" and check.get("checked"):
                check["fallback"] = "template"

        state.stats = stats
        state.summary = {
            "headline": headline, "narrative": narrative, "recommendations": recs,
            "number_check": check, "generator": generator,
        }
        if ctx.session_factory is not None:
            values = {
                "run_id": ctx.run_id, "stats": stats, "retrieval": state.retrieval, "headline": headline,
                "narrative": narrative, "recommendations": recs, "number_check": check, "generator": generator,
            }
            async with ctx.session_factory() as s:
                stmt = pg_insert(RunSummary).values(**values)
                stmt = stmt.on_conflict_do_update(index_elements=["run_id"], set_={k: v for k, v in values.items() if k != "run_id"})
                await s.execute(stmt)
                await s.commit()
        await ctx.emitter.emit(self.name, "result", headline, {"generator": generator})
