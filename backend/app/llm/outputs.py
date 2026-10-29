"""Structured-output schemas for LLM calls (flat and fully required, so they convert cleanly for Gemini)."""

from typing import Literal

from pydantic import BaseModel, Field


class EmailDraftOut(BaseModel):
    customer_ref: str
    subject: str
    body: str


class EmailBatchOut(BaseModel):
    emails: list[EmailDraftOut]


class AdjudicationOut(BaseModel):
    case_id: str
    choice: Literal["APPLY_RULE_PLAN", "DELAY_ALL"]
    rationale: str = Field(description="One or two sentences grounded in the case facts.")


class AdjudicationBatchOut(BaseModel):
    decisions: list[AdjudicationOut]


INVESTIGATOR_TOOLS = ("delivery_forecast", "alternatives", "compare_plans", "customer_orders", "policy")


class ToolCallOut(BaseModel):
    tool: Literal["delivery_forecast", "alternatives", "compare_plans", "customer_orders", "policy"]
    argument: str = Field(description="Search text for the policy tool; empty string for the other tools.")


class InvestigationStepOut(BaseModel):
    thought: str = Field(description="One sentence: what you still need to find out, or why you can decide now.")
    tool_calls: list[ToolCallOut] = Field(description="Tools to run before deciding; empty when deciding.")
    decision: Literal["CONTINUE", "APPLY_RULE_PLAN", "DELAY_ALL"] = Field(description="CONTINUE while investigating.")
    rationale: str = Field(description="When deciding: one or two sentences that cite tool results. Otherwise empty.")


class NarrativeOut(BaseModel):
    headline: str
    narrative: str
