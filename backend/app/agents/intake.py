"""Intake agent: turns the free-text disruption into a validated DisruptionSpec."""

from __future__ import annotations

from app.agents.base import AgentContext, RunState
from app.services.intake_service import interpret
from app.services.spec_builder import apply_ambiguities


class IntakeAgent:
    name = "intake"
    label = "Interpreting the disruption"

    async def run(self, state: RunState, ctx: AgentContext) -> None:
        if state.spec is not None:
            state.spec = apply_ambiguities(state.spec)
            state.spec_source = state.spec.source
            await ctx.emitter.emit(
                self.name, "info",
                "Using the interpretation confirmed by the analyst.",
                {"spec_source": state.spec.source},
            )
        else:
            result = await interpret(state.raw_query or "", ctx.ref, ctx.llm, use_llm=ctx.use_llm, run_id=ctx.run_id)
            state.spec = result.spec
            state.spec_source = result.source
            state.intake_warnings = result.warnings
            for w in result.warnings:
                await ctx.emitter.emit(self.name, "warning", w, level="warning")
        spec = state.spec
        assert spec is not None
        await ctx.emitter.emit(
            self.name, "result",
            f"{spec.disruption_type.title()} disruption: origin {spec.origin.describe()}; "
            f"destination {spec.destination.describe()}; {spec.duration_days:g} days"
            + (f" + {spec.recovery_days:g} recovery" if spec.recovery_days else "")
            + f"; legs {', '.join(spec.legs)}.",
            {
                "source": state.spec_source,
                "confidence": spec.confidence,
                "ambiguities": [a.mention for a in spec.ambiguities],
                "nationwide": spec.nationwide,
                "category_terms": spec.category_terms,
            },
        )
