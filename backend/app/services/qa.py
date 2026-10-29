"""QA critic for customer emails: grounding, policy compliance, required content, tone and length."""

from __future__ import annotations

import re

from pydantic import BaseModel

from app.services.comms import PLACEHOLDER_URLS, VOUCHER_PLACEHOLDER, CustomerFacts, QAAllowances
from app.services.fact_check import scan

PLACEHOLDER_RE = re.compile(r"\[\[[A-Z_]+\]\]")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
APOLOGY_RE = re.compile(r"\b(sorry|apologi[sz]e|apologies|desculp\w*|lamentamos|sentimos muito)\b", re.I)
JARGON_RE = re.compile(
    r"\b(severity|sla|p90|p50|linehaul|line-haul|first[- ]mile|last[- ]mile|rule\s?R\d|R[0-8]\b|p\(breach\)|"
    r"breach probability|adjudicat\w*|llm|algorithm)\b",
    re.I,
)
GUARANTEE_RE = re.compile(
    r"\b(guarantee[ds]?|definitely|certainly will|promise you|without fail|garantimos|garantido|com certeza)\b", re.I
)
VOUCHER_WORDS_RE = re.compile(r"\b(voucher|coupon|discount code|cupom|cupons)\b", re.I)
REFUND_WORDS_RE = re.compile(r"\b(refund\w*|reembols\w*)\b", re.I)
LOCALE_HINTS = {
    "en": re.compile(r"\b(your|order|we)\b", re.I),
    "pt-BR": re.compile(r"\b(seu|sua|pedido|voc[eê])\b", re.I),
}
MIN_WORDS, MAX_WORDS = 70, 320


class QACheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class QAReport(BaseModel):
    passed: bool
    checks: list[QACheck]

    def failures(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.passed]


def check_email(subject: str, body: str, facts: CustomerFacts, allow: QAAllowances) -> QAReport:
    """Check an email in placeholder form (use comms.unrender first for rendered or human-edited text)."""
    checks: list[QACheck] = []
    full = f"{subject}\n{body}"

    used = set(PLACEHOLDER_RE.findall(full))
    illegal = sorted(used - set(facts.allowed_placeholders))
    checks.append(QACheck(
        name="placeholders", passed=not illegal,
        detail=("Not allowed: " + ", ".join(illegal)) if illegal else "Only allowed placeholders used.",
    ))
    if facts.has_voucher:
        checks.append(QACheck(
            name="voucher_code", passed=VOUCHER_PLACEHOLDER in body,
            detail="The voucher code placeholder must appear in the body." if VOUCHER_PLACEHOLDER not in body else "Voucher code included.",
        ))

    urls = URL_RE.findall(full)
    checks.append(QACheck(
        name="no_raw_links", passed=not urls,
        detail=("Raw links are not allowed (use placeholders): " + ", ".join(urls[:3])) if urls else "No raw links.",
    ))

    problems = scan(
        full,
        allowed_dates=allow.dates,
        allowed_money=allow.money,
        allowed_percents=allow.percents,
        allowed_numbers=allow.numbers,
        strip_tokens=[
            *allow.order_refs, *PLACEHOLDER_URLS.keys(), VOUCHER_PLACEHOLDER, facts.first_name,
            # Item labels come from the facts sheet ("1 x Home Appliances"); strip the name part so that a
            # digit inside a product name can never be mistaken for an ungrounded number.
            *sorted({item.split(" x ", 1)[-1] for o in facts.orders for item in o.items + o.cancelled_items}, key=len, reverse=True),
        ],
    )
    checks.append(QACheck(
        name="facts_grounded", passed=not problems,
        detail="; ".join(problems[:6]) if problems else "Every date, amount and number matches the facts.",
    ))

    if not facts.has_voucher and VOUCHER_WORDS_RE.search(full):
        checks.append(QACheck(name="policy_voucher", passed=False, detail="Mentions a voucher but none is granted."))
    has_refund = any(o.freight_refund or o.refund_total for o in facts.orders)
    if not has_refund and not facts.has_cancellation_offer and REFUND_WORDS_RE.search(full):
        checks.append(QACheck(name="policy_refund", passed=False, detail="Mentions a refund but none is granted."))
    guarantee = GUARANTEE_RE.search(full)
    checks.append(QACheck(
        name="no_guarantees", passed=guarantee is None,
        detail=f"Avoid guarantees: '{guarantee.group(0)}'" if guarantee else "No guarantees made.",
    ))
    jargon = JARGON_RE.search(full)
    checks.append(QACheck(
        name="no_internal_jargon", passed=jargon is None,
        detail=f"Internal term used: '{jargon.group(0)}'" if jargon else "No internal jargon.",
    ))

    missing: list[str] = []
    if not APOLOGY_RE.search(body):
        missing.append("an apology")
    for o in facts.orders:
        if o.order_ref not in full:
            missing.append(f"order reference {o.order_ref}")
        if o.resolution in ("DELAY_WITH_COMPENSATION", "REROUTE_ALT_SELLER", "REROUTE_ALT_ROUTE") and o.new_window_start not in body:
            missing.append(f"the new delivery date {o.new_window_start}")
    if facts.first_name not in body:
        missing.append("the customer's first name")
    checks.append(QACheck(
        name="required_content", passed=not missing,
        detail=("Missing " + ", ".join(missing)) if missing else "Apology, order references, dates and greeting present.",
    ))

    words = len(re.findall(r"\w+", body))
    checks.append(QACheck(
        name="length", passed=MIN_WORDS <= words <= MAX_WORDS,
        detail=f"{words} words (allowed {MIN_WORDS}-{MAX_WORDS}).",
    ))
    subject_ok = 5 <= len(subject) <= 120
    checks.append(QACheck(name="subject", passed=subject_ok, detail=f"{len(subject)} characters."))
    hint = LOCALE_HINTS.get(facts.locale)
    if hint is not None:
        checks.append(QACheck(
            name="language", passed=bool(hint.search(body)),
            detail=f"Expected {facts.locale} text." if not hint.search(body) else f"Written in {facts.locale}.",
        ))
    return QAReport(passed=all(c.passed for c in checks), checks=checks)
