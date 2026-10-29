"""Logistics Analyst agent: deterministic ETA, breach probability and severity per order."""

from __future__ import annotations

import asyncio
from collections import Counter

from app.agents.base import AgentContext, RunState
from app.services.analysis import compute_order_impacts


class LogisticsAnalystAgent:
    name = "analyst"
    label = "Computing delays, SLA breach risk and severity"

    async def run(self, state: RunState, ctx: AgentContext) -> None:
        assert state.spec is not None and state.scope is not None
        impacts = await asyncio.to_thread(
            compute_order_impacts, state.spec, ctx.ref, state.scope.matched, ctx.scoring
        )
        state.impacts = impacts
        delayed = [i for i in impacts if i.affected]
        breaching = [i for i in delayed if i.days_late_calendar > 0]
        tiers = Counter(i.severity_tier for i in delayed)
        await ctx.emitter.emit(
            self.name, "result",
            f"{len(delayed)} of {len(impacts)} orders in scope are delayed; {len(breaching)} will miss the promised date. "
            f"Severity: {tiers.get('critical', 0)} critical, {tiers.get('high', 0)} high, "
            f"{tiers.get('medium', 0)} medium, {tiers.get('low', 0)} low.",
            {"delayed": len(delayed), "breaching": len(breaching), "tiers": dict(tiers)},
        )
