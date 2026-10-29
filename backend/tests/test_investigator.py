"""Tool-using investigator: deterministic tools, bounded loop, decision handling (LLM replaced by a script)."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.agents.investigator import CaseTools, investigate
from app.core.config import get_settings
from app.llm.outputs import InvestigationStepOut, ToolCallOut
from app.services.impact import order_impact
from app.services.policy_engine import decide_order
from app.services.retrieval.kb import kb_index
from app.services.scope import ConceptFilter, match_line
from tests.conftest import make_line, sp_spec


class ScriptedLLM:
    def __init__(self, steps: list[InvestigationStepOut]):
        self.steps = steps
        self.calls: list[dict] = []

    async def generate(self, **kw):
        self.calls.append(kw["context"])
        return SimpleNamespace(parsed=self.steps[min(len(self.calls), len(self.steps)) - 1])


@pytest.fixture
def tools(engine_ctx, policy_ctx_factory):
    spec = sp_spec()
    line = make_line(handling_profile="BULKY", billable_weight_kg=35.0, sla_ts=datetime(2018, 8, 8))
    m = match_line(spec, line, ConceptFilter())
    imp = order_impact([line], {line.order_item_id: m}, spec, engine_ctx)
    pc = policy_ctx_factory(spec)
    dec = decide_order(imp, pc)
    return CaseTools(imp=imp, dec=dec, pc=pc, kb=list(kb_index(get_settings().kb_dir).values()), customer_impacts=[imp])


def step(decision="CONTINUE", calls=(), rationale=""):
    return InvestigationStepOut(thought="t", tool_calls=[ToolCallOut(tool=t, argument=a) for t, a in calls],
                                decision=decision, rationale=rationale)


def test_tools_return_engine_facts(tools):
    plans = tools.run("compare_plans", "")
    assert plans["APPLY_RULE_PLAN"]["days_late"] == tools.dec.resolution_days_late
    assert set(plans) == {"APPLY_RULE_PLAN", "DELAY_ALL"}
    fc = tools.run("delivery_forecast", "")
    assert fc["days_late"] == tools.imp.days_late_calendar and fc["delayed_items"]
    assert tools.run("customer_orders", "")["other_affected_orders"] == 0
    assert tools.run("policy", "split shipments")["sections"]
    assert "error" in tools.run("drop_tables", "")


@pytest.mark.asyncio
async def test_investigates_then_decides(tools):
    llm = ScriptedLLM([
        step(calls=[("compare_plans", ""), ("alternatives", ""), ("policy", "bulky delay compensation")]),
        step("DELAY_ALL", rationale="Rerouting gains nothing for a bulky item; compensate instead."),
    ])
    choice, rationale, trace = await investigate(llm, tools, run_id=None, max_steps=3)
    assert choice == "DELAY_ALL" and "bulky" in rationale
    assert [t["tool"] for t in trace[0]["tools"]] == ["compare_plans", "alternatives", "policy"]
    assert trace[-1]["decision"] == "DELAY_ALL"
    assert "compare_plans" in llm.calls[1]["transcript"]  # tool results are fed back to the model


@pytest.mark.asyncio
async def test_loop_is_bounded_and_forces_a_decision(tools):
    llm = ScriptedLLM([step(calls=[("delivery_forecast", "")])])  # never decides
    choice, _, trace = await investigate(llm, tools, run_id=None, max_steps=3)
    assert choice is None and len(llm.calls) == 3 and len(trace) == 3
    assert [c["must_decide"] for c in llm.calls] == [False, False, True]
