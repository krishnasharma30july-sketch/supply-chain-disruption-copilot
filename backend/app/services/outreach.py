"""Outreach for people: care-team call briefs and notices to sellers.

Both are grounded the same way as customer emails. The LLM only sees a pre-formatted fact sheet and must copy its
dates, amounts and counts; a deterministic checker verifies every figure, and a deterministic template is the fallback
when the LLM is unavailable or keeps failing the check.
"""

from __future__ import annotations

import re
from datetime import timedelta

from pydantic import BaseModel

from app.domain.models import DisruptionSpec, OrderImpact
from app.llm.outputs import CallBriefOut, SellerNoticeOut
from app.services.comms import CustomerFacts, cause_text
from app.services.fact_check import scan
from app.services.formatting import fmt_date, fmt_money
from app.services.qa import GUARANTEE_RE, JARGON_RE, PLACEHOLDER_RE, URL_RE, QACheck, QAReport, check_brief

# ----------------------------------------------------------------------------------------------- call briefs


def brief_body(b: CallBriefOut, include_avoid: bool = True) -> str:
    """Plain text of a call brief (what the care agent copies and what the checker reads)."""
    lines = [f"Opening: {b.opening.strip()}", f"Situation: {b.situation.strip()}", "Talking points:"]
    lines += [f"- {p.strip()}" for p in b.talking_points]
    lines.append(f"Already offered: {b.already_offered.strip()}")
    if include_avoid:
        lines.append("Avoid:")
        lines += [f"- {a.strip()}" for a in b.avoid]
    lines.append(f"Next step: {b.next_step.strip()}")
    return "\n".join(lines)


def check_call_brief(b: CallBriefOut, facts: CustomerFacts, allow) -> QAReport:
    """Fact-check a brief. Figures are checked in the whole text; the guarantee and internal-term word rules skip the
    "avoid" list, which names what the agent must not say ("do not guarantee a date")."""
    return check_brief(brief_body(b), facts, allow, lexical_text=brief_body(b, include_avoid=False))


def _offered(f: CustomerFacts, pt: bool) -> str:
    parts: list[str] = []
    for o in f.orders:
        bits: list[str] = []
        if o.freight_refund:
            bits.append(("reembolso do frete de " if pt else "a freight refund of ") + o.freight_refund)
        if o.voucher_amount:
            bits.append((f"um voucher de {o.voucher_amount} ({o.voucher_percent})") if pt else f"a voucher of {o.voucher_amount} ({o.voucher_percent})")
        if o.refund_total:
            bits.append(("reembolso total de " if pt else "a refund total of ") + o.refund_total)
        if o.offer_cancellation:
            bits.append("possibilidade de cancelar o pedido" if pt else "the option to cancel the order")
        if bits:
            parts.append(f"{o.order_ref}: " + ", ".join(bits))
    if not parts:
        return "Nada até agora." if pt else "Nothing yet."
    return "; ".join(parts) + "."


def template_brief(f: CustomerFacts) -> CallBriefOut:
    pt = f.locale == "pt-BR"
    refs = ", ".join(o.order_ref for o in f.orders)
    situation_parts = [("Causa: " if pt else "Cause: ") + f.cause + "."]
    for o in f.orders:
        items = ", ".join(o.items)
        late = (" O pedido já estava atrasado antes desta interrupção." if pt else " It was already running behind its promised date before this disruption.") if o.already_late_before_disruption else ""
        situation_parts.append(
            (f"Pedido {o.order_ref} ({items}) tinha entrega prometida para {o.promised_date}. {o.resolution_summary}{late}")
            if pt else
            f"Order {o.order_ref} ({items}) was promised for {o.promised_date}. {o.resolution_summary}{late}"
        )
    points = (
        [
            "Peça desculpas e explique a causa em poucas palavras, sem culpar ninguém.",
            *[f"Pedido {o.order_ref}: {o.resolution_summary}" for o in f.orders],
            "Pergunte se o cliente quer alterar algo no pedido.",
            *(["Lembre que o cliente pode cancelar o pedido, se preferir."] if f.has_cancellation_offer else []),
        ]
        if pt else
        [
            "Apologise and explain the cause briefly, without blaming anyone.",
            *[f"Order {o.order_ref}: {o.resolution_summary}" for o in f.orders],
            "Ask whether the customer wants to change anything about the order.",
            *(["Remind the customer that cancelling the order is possible if they prefer."] if f.has_cancellation_offer else []),
        ]
    )
    if pt:
        return CallBriefOut(
            customer_ref=f.customer_ref,
            opening=f"Olá {f.first_name}, aqui é a equipe de Atendimento ao Cliente. Ligo sobre o pedido {refs} e peço desculpas pelo atraso.",
            situation=" ".join(situation_parts),
            talking_points=points,
            already_offered=_offered(f, True),
            avoid=["Não prometa uma data de entrega: diga que esperamos entregar na janela informada.",
                   "Não ofereça nada além do que já foi oferecido.", "Não fale de sistemas internos nem de pontuações."],
            next_step="Registre o resultado da ligação no histórico do caso e sinalize o pedido se o cliente pedir o cancelamento.",
        )
    return CallBriefOut(
        customer_ref=f.customer_ref,
        opening=f"Hello {f.first_name}, this is the Customer Care Team calling about your order {refs}. I am sorry for the delay and would like to explain what is happening.",
        situation=" ".join(situation_parts),
        talking_points=points,
        already_offered=_offered(f, False),
        avoid=["Do not promise a delivery date: say that we expect the order in the window given.",
               "Do not offer anything beyond what is already listed.", "Do not mention internal systems or scores."],
        next_step="Log the outcome of the call in the case notes and flag the order if the customer asks to cancel.",
    )


# ----------------------------------------------------------------------------------------------- seller notices


class SellerFacts(BaseModel):
    seller_ref: str
    location: str
    locale: str
    cause: str
    window_start: str
    window_end: str
    recovery_end: str
    orders_affected: int
    awaiting_pickup: int
    in_transit: int
    pending_approval: int
    bulky_orders: int
    value: str


class SellerAllow(BaseModel):
    dates: list[str]
    money: list[float]
    numbers: list[float]


def build_seller_facts(
    impacts: list[OrderImpact], spec: DisruptionSpec, locale: str, limit: int
) -> list[tuple[SellerFacts, SellerAllow]]:
    """The most exposed sellers (by affected orders, then value), each with a pre-formatted fact sheet."""
    per: dict[str, dict] = {}
    for imp in impacts:
        if not imp.affected:
            continue
        for li in imp.lines:
            if not li.affected:
                continue
            e = per.setdefault(li.seller_id, {"city": li.seller_city, "state": li.seller_state, "orders": {}, "value": 0.0, "bulky": set()})
            e["orders"].setdefault(imp.order_id, imp.stage)
            e["value"] += li.price + li.freight  # the same definition as the report's "most exposed sellers"
            if li.handling_profile == "BULKY":
                e["bulky"].add(imp.order_id)
    # Same ranking as the report (affected orders, then seller id), so the notices cover exactly the sellers it lists.
    ranked = sorted(per.items(), key=lambda kv: (-len(kv[1]["orders"]), kv[0]))[: max(0, limit)]
    start, end = spec.start_at, spec.end_at
    recovery = end + timedelta(days=spec.recovery_days)
    out: list[tuple[SellerFacts, SellerAllow]] = []
    for seller_id, e in ranked:
        stages = list(e["orders"].values())
        facts = SellerFacts(
            seller_ref=seller_id[:8], location=f"{e['city'].title()}/{e['state']}", locale=locale,
            cause=cause_text(spec, locale), window_start=fmt_date(start, locale), window_end=fmt_date(end, locale),
            recovery_end=fmt_date(recovery, locale), orders_affected=len(e["orders"]),
            awaiting_pickup=stages.count("awaiting_pickup"), in_transit=stages.count("in_transit"),
            pending_approval=stages.count("pending_approval"), bulky_orders=len(e["bulky"]),
            value=fmt_money(e["value"], locale),
        )
        allow = SellerAllow(
            dates=sorted({facts.window_start, facts.window_end, facts.recovery_end}, key=len, reverse=True),
            money=[round(e["value"], 2)],
            numbers=sorted({1.0, float(facts.orders_affected), float(facts.awaiting_pickup), float(facts.in_transit),
                            float(facts.pending_approval), float(facts.bulky_orders)}),
        )
        out.append((facts, allow))
    return out


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def template_seller_notice(f: SellerFacts) -> SellerNoticeOut:
    if f.locale == "pt-BR":
        breakdown = ", ".join(
            x for x in (
                _n(f.awaiting_pickup, "aguardando coleta", "aguardando coleta") if f.awaiting_pickup else "",
                _n(f.in_transit, "em trânsito", "em trânsito") if f.in_transit else "",
                _n(f.pending_approval, "com pagamento pendente", "com pagamento pendente") if f.pending_approval else "",
            ) if x
        )
        body = (
            f"Olá, equipe do vendedor {f.seller_ref},\n\n"
            f"Estamos entrando em contato por causa de {f.cause}. Esperamos interrupções no transporte entre {f.window_start} e {f.window_end}, "
            f"com recuperação até {f.recovery_end}.\n\n"
            f"{_n(f.orders_affected, 'pedido seu está afetado', 'pedidos seus estão afetados')}, somando {f.value}"
            + (f" ({breakdown})" if breakdown else "") + ".\n\n"
            "Pedimos que você:\n"
            "- Segure as coletas dos pedidos aguardando retirada até avisarmos que as transportadoras voltaram a circular.\n"
            "- Nos diga se consegue replanejar ou despachar algum pedido por outra rota.\n"
            "- Confirme o estoque dos produtos afetados, para que possamos redirecionar pedidos quando necessário.\n\n"
            "Responda esta mensagem se tiver dúvidas.\n\nOperações do Marketplace"
        )
        subject = f"Aviso aos vendedores: {_n(f.orders_affected, 'pedido afetado', 'pedidos afetados')} ({f.seller_ref})"
        return SellerNoticeOut(seller_ref=f.seller_ref, subject=subject, body=body)
    breakdown = ", ".join(
        x for x in (
            f"{f.awaiting_pickup} awaiting pickup" if f.awaiting_pickup else "",
            f"{f.in_transit} in transit" if f.in_transit else "",
            f"{f.pending_approval} pending payment approval" if f.pending_approval else "",
        ) if x
    )
    body = (
        f"Hello {f.seller_ref} team,\n\n"
        f"We are writing about {f.cause}. We expect freight disruption between {f.window_start} and {f.window_end}, "
        f"with recovery until {f.recovery_end}.\n\n"
        f"{_n(f.orders_affected, 'order of yours is', 'orders of yours are')} affected, worth {f.value} in total"
        + (f" ({breakdown})" if breakdown else "") + ".\n\n"
        "Please:\n"
        "- Hold handovers for orders awaiting pickup until we confirm that carriers are moving again.\n"
        "- Tell us if you can re-plan or dispatch any of these orders by another route.\n"
        "- Confirm stock for the affected products, so that we can re-source orders when needed.\n\n"
        "Reply to this message if you have any questions.\n\nMarketplace Operations"
    )
    subject = f"Disruption notice for seller {f.seller_ref}: {_n(f.orders_affected, 'order', 'orders')} affected"
    return SellerNoticeOut(seller_ref=f.seller_ref, subject=subject, body=body)


PROMISE_RE = re.compile(
    r"\b(compensat\w*|refund\w*|reimburs\w*|penalt\w*|fines|voucher|coupon|compensa\w+|reembols\w*|multa\w*|penalidade\w*|cupom|cupons)\b",
    re.I,
)
SELLER_MIN_WORDS, SELLER_MAX_WORDS = 50, 230


def check_seller_notice(subject: str, body: str, facts: SellerFacts, allow: SellerAllow) -> QAReport:
    full = f"{subject}\n{body}"
    checks: list[QACheck] = []
    placeholders = sorted(set(PLACEHOLDER_RE.findall(full)))
    checks.append(QACheck(name="no_placeholders", passed=not placeholders,
                          detail=("Placeholders are not used: " + ", ".join(placeholders)) if placeholders else "No placeholders."))
    urls = URL_RE.findall(full)
    checks.append(QACheck(name="no_raw_links", passed=not urls,
                          detail=("Links are not allowed: " + ", ".join(urls[:3])) if urls else "No raw links."))
    problems = scan(
        full, allowed_dates=allow.dates, allowed_money=allow.money, allowed_percents=[], allowed_numbers=allow.numbers,
        strip_tokens=[facts.seller_ref, facts.location],
    )
    checks.append(QACheck(name="facts_grounded", passed=not problems,
                          detail="; ".join(problems[:6]) if problems else "Every date, amount and number matches the facts."))
    promise = PROMISE_RE.search(full)
    checks.append(QACheck(name="no_compensation_promises", passed=promise is None,
                          detail=f"No compensation, fees or penalties may be mentioned: '{promise.group(0)}'" if promise else "No compensation promised."))
    guarantee = GUARANTEE_RE.search(full)
    checks.append(QACheck(name="no_guarantees", passed=guarantee is None,
                          detail=f"Avoid guarantees: '{guarantee.group(0)}'" if guarantee else "No guarantees made."))
    jargon = JARGON_RE.search(full)
    checks.append(QACheck(name="no_internal_jargon", passed=jargon is None,
                          detail=f"Internal term used: '{jargon.group(0)}'" if jargon else "No internal jargon."))
    missing = [x for x in (facts.seller_ref, str(facts.orders_affected)) if x not in full]
    checks.append(QACheck(name="required_content", passed=not missing,
                          detail=("Missing " + ", ".join(missing)) if missing else "Seller reference and order count present."))
    words = len(re.findall(r"\w+", body))
    checks.append(QACheck(name="length", passed=SELLER_MIN_WORDS <= words <= SELLER_MAX_WORDS,
                          detail=f"{words} words (allowed {SELLER_MIN_WORDS}-{SELLER_MAX_WORDS})."))
    checks.append(QACheck(name="subject", passed=5 <= len(subject) <= 120, detail=f"{len(subject)} characters."))
    return QAReport(passed=all(c.passed for c in checks), checks=checks)
