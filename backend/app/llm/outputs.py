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
    sources: list[str] = Field(
        default_factory=list,
        description="Ids of the policy sections from retrieved_sources that the summary relies on; empty when none.",
    )


class ToneReviewOut(BaseModel):
    customer_ref: str
    warmth: int = Field(description="1 (cold or robotic) to 5 (sincere and human).")
    clarity: int = Field(description="1 (confusing) to 5 (the customer instantly knows what happened and what happens next).")
    notes: str = Field(description="One sentence: the most useful concrete wording improvement, or 'None.' for a 5/5 email.")


class ToneBatchOut(BaseModel):
    reviews: list[ToneReviewOut]


class CallBriefOut(BaseModel):
    customer_ref: str
    opening: str = Field(description="One or two sentences the agent can say to open the call.")
    situation: str = Field(description="Two or three plain sentences on what happened to the customer's order(s).")
    talking_points: list[str] = Field(description="Three to five short points to cover, in order.")
    already_offered: str = Field(description="What the customer has already been offered, exactly as in the facts; say 'Nothing yet.' if empty.")
    avoid: list[str] = Field(description="Two to four things the agent must not promise or say.")
    next_step: str = Field(description="The one follow-up action the agent should take after the call.")


class CallBriefBatchOut(BaseModel):
    briefs: list[CallBriefOut]


class SellerNoticeOut(BaseModel):
    seller_ref: str
    subject: str
    body: str


class SellerNoticeBatchOut(BaseModel):
    notices: list[SellerNoticeOut]
