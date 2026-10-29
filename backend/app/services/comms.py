"""Customer communication facts, deterministic templates and placeholder rendering.

The LLM only ever sees a CustomerFacts sheet: pre-formatted dates and amounts it must copy verbatim. The
same sheet feeds the template fallback and the QA allow-lists, so every channel is grounded identically.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.domain.models import DisruptionSpec, MitigationDecision, OrderImpact
from app.services.formatting import fmt_date, fmt_money, fmt_pct, pretty_category, pretty_category_pt
from app.services.geo import STATE_NAMES

TRACK_LINK = "https://shop.example.com/account/orders"
CANCEL_LINK = "https://shop.example.com/account/orders/cancel"
SUPPORT_LINK = "https://shop.example.com/help"
PLACEHOLDER_URLS = {
    "[[TRACK_LINK]]": TRACK_LINK,
    "[[CANCEL_LINK]]": CANCEL_LINK,
    "[[SUPPORT_LINK]]": SUPPORT_LINK,
}
VOUCHER_PLACEHOLDER = "[[VOUCHER_CODE]]"
VOUCHER_VALID_DAYS = 90


class OrderFacts(BaseModel):
    order_ref: str
    purchase_date: str
    items: list[str]
    promised_date: str
    new_window_start: str
    new_window_end: str
    days_late: int
    # True when the order would have missed its promise even without the disruption: emails must say so
    # instead of presenting the disruption as the only cause.
    already_late_before_disruption: bool = False
    resolution: str
    resolution_summary: str
    cancelled_items: list[str] = Field(default_factory=list)
    split_shipment: bool = False
    offer_cancellation: bool = False
    freight_refund: str | None = None
    voucher_percent: str | None = None
    voucher_amount: str | None = None
    refund_total: str | None = None
    personal_follow_up: bool = False


class CustomerFacts(BaseModel):
    customer_ref: str
    first_name: str
    locale: str
    cause: str
    orders: list[OrderFacts]
    has_voucher: bool
    has_cancellation_offer: bool
    allowed_placeholders: list[str]


class QAAllowances(BaseModel):
    dates: list[str]
    money: list[float]
    percents: list[float]
    numbers: list[float]
    order_refs: list[str]
    voucher_code: str | None


CAUSE = {
    "en": {
        "weather": "severe weather", "strike": "a transport strike",
        "infrastructure": "a road and infrastructure disruption",
        "capacity": "a temporary capacity shortage in our delivery network",
        "supplier": "a supply issue at one of our sellers", "seller_outage": "an operational issue at one of our sellers",
        "other": "an unexpected logistics disruption",
    },
    "pt-BR": {
        "weather": "condições climáticas severas", "strike": "uma greve no transporte",
        "infrastructure": "um bloqueio em rodovias e infraestrutura",
        "capacity": "uma falta temporária de capacidade na nossa rede de entregas",
        "supplier": "um problema de abastecimento em um de nossos vendedores",
        "seller_outage": "um problema operacional em um de nossos vendedores",
        "other": "um imprevisto logístico",
    },
}


def order_ref(order_id: str) -> str:
    return order_id[:8].upper()


def voucher_code(run_id: str, customer_unique_id: str) -> str:
    digest = hashlib.sha1(f"{run_id}:{customer_unique_id}".encode()).hexdigest()[:6].upper()
    return f"SORRY-{digest}"


def cause_text(spec: DisruptionSpec, locale: str) -> str:
    base = CAUSE.get(locale, CAUSE["en"]).get(spec.disruption_type, CAUSE["en"]["other"])
    if spec.nationwide:
        return base + (" em todo o Brasil" if locale == "pt-BR" else " across Brazil")
    places: list[str] = []
    for geo in (spec.origin, spec.destination):
        for c in geo.cities:
            label = c.label.split(",")[0]
            if label not in places:
                places.append(label)
        for s in geo.states:
            name = STATE_NAMES.get(s, s)
            if name not in places:
                places.append(name)
        if geo.radius and geo.radius.center.label.split(",")[0] not in places:
            places.append(geo.radius.center.label.split(",")[0])
    if not places:
        return base
    if len(places) > 3:
        places = places[:3]
    joiner = " e " if locale == "pt-BR" else " and "
    where = ", ".join(places[:-1]) + joiner + places[-1] if len(places) > 1 else places[0]
    return f"{base} {'em' if locale == 'pt-BR' else 'in'} {where}"


def _item_label(li, locale: str) -> str:
    if locale == "pt-BR" and li.category_pt:
        return pretty_category_pt(li.category_pt)
    return pretty_category(li.category_en)


def _items(impact: OrderImpact, locale: str, item_ids: set[int] | None = None) -> list[str]:
    counts = Counter(
        _item_label(li, locale) for li in impact.lines if item_ids is None or li.order_item_id in item_ids
    )
    return [f"{n} x {name}" for name, n in counts.items()]


def _summary(dec: MitigationDecision, start: str, end: str, locale: str) -> str:
    if locale == "pt-BR":
        window = f"por volta de {start}" if start == end else f"entre {start} e {end}"
        texts = {
            "NOTIFY_ONLY": "Seu pedido ainda deve chegar dentro do prazo prometido, mas pode chegar um pouco depois do que o rastreamento indicava.",
            "DELAY_WITH_COMPENSATION": f"Seu pedido continua a caminho e agora a previsão de entrega é {window}.",
            "REROUTE_ALT_SELLER": f"Vamos enviar o mesmo produto a partir de outro vendedor fora da área afetada, com entrega prevista {window}.",
            "REROUTE_ALT_ROUTE": f"Transferimos sua encomenda para uma rota de transporte alternativa, com entrega prevista {window}.",
            "CANCEL_REFUND": "Como o item é perecível, cancelamos o item e faremos o reembolso integral.",
        }
    else:
        window = f"on or around {start}" if start == end else f"between {start} and {end}"
        texts = {
            "NOTIFY_ONLY": "Your order is still expected by the promised date, but it may arrive a little later than tracking previously showed.",
            "DELAY_WITH_COMPENSATION": f"Your order is still on its way and is now expected {window}.",
            "REROUTE_ALT_SELLER": f"We are sending the same product from another seller outside the affected area, now expected {window}.",
            "REROUTE_ALT_ROUTE": f"We moved your parcel to an alternative transport route, now expected {window}.",
            "CANCEL_REFUND": "Because the item is perishable, we have cancelled it and will refund it in full.",
        }
    return texts.get(dec.action, texts["DELAY_WITH_COMPENSATION"])


def build_customer_facts(
    customer_unique_id: str,
    first_name: str,
    pairs: list[tuple[OrderImpact, MitigationDecision]],
    spec: DisruptionSpec,
    locale: str,
    run_id: str,
    date_shift_days: int = 0,
) -> tuple[CustomerFacts, QAAllowances]:
    shift = timedelta(days=date_shift_days)

    def d(dt: datetime) -> str:
        return fmt_date(dt + shift, locale)

    orders: list[OrderFacts] = []
    dates: list[str] = []
    money: list[float] = []
    percents: list[float] = []
    numbers: list[float] = [float(len(pairs)), float(VOUCHER_VALID_DAYS)]
    has_voucher = False
    has_cancel = False
    for imp, dec in pairs:
        start, end = d(dec.resolution_eta), d(dec.resolution_eta_p90)
        cancelled_ids = {ld.order_item_id for ld in dec.line_decisions if ld.action == "CANCEL_REFUND"}
        comp = dec.compensation
        of = OrderFacts(
            order_ref=order_ref(imp.order_id),
            purchase_date=d(imp.purchase_ts),
            items=_items(imp, locale),
            promised_date=d(imp.sla_ts),
            new_window_start=start,
            new_window_end=end,
            days_late=dec.resolution_days_late,
            already_late_before_disruption=imp.baseline_eta.date() > imp.sla_ts.date(),
            resolution=dec.action,
            resolution_summary=_summary(dec, start, end, locale),
            cancelled_items=_items(imp, locale, cancelled_ids) if cancelled_ids else [],
            split_shipment=dec.split_shipment,
            offer_cancellation=dec.offer_cancellation,
            freight_refund=fmt_money(comp.freight_refund_brl, locale) if comp.freight_refund_brl > 0 else None,
            voucher_percent=fmt_pct(comp.voucher_pct) if comp.voucher_brl > 0 else None,
            voucher_amount=fmt_money(comp.voucher_brl, locale) if comp.voucher_brl > 0 else None,
            refund_total=fmt_money(comp.refund_total_brl, locale) if comp.refund_total_brl > 0 else None,
            personal_follow_up=comp.human_callback,
        )
        orders.append(of)
        dates += [of.purchase_date, of.promised_date, start, end]
        money += [v for v in (comp.freight_refund_brl, comp.voucher_brl, comp.refund_total_brl) if v > 0]
        if comp.voucher_brl > 0:
            percents.append(comp.voucher_pct)
            has_voucher = True
        has_cancel = has_cancel or dec.offer_cancellation
        numbers.append(float(dec.resolution_days_late))
        numbers.append(float(len(imp.lines)))
        for item in of.items + of.cancelled_items:
            numbers.append(float(item.split(" x ")[0]))

    placeholders = ["[[TRACK_LINK]]", "[[SUPPORT_LINK]]"]
    if has_voucher:
        placeholders.append(VOUCHER_PLACEHOLDER)
    if has_cancel:
        placeholders.append("[[CANCEL_LINK]]")
    facts = CustomerFacts(
        customer_ref=customer_unique_id[:8],
        first_name=first_name,
        locale=locale,
        cause=cause_text(spec, locale),
        orders=orders,
        has_voucher=has_voucher,
        has_cancellation_offer=has_cancel,
        allowed_placeholders=placeholders,
    )
    allow = QAAllowances(
        dates=sorted(set(dates), key=len, reverse=True),
        money=sorted(set(round(m, 2) for m in money)),
        percents=sorted(set(percents)),
        numbers=sorted(set(numbers)),
        order_refs=[o.order_ref for o in orders],
        voucher_code=voucher_code(run_id, customer_unique_id) if has_voucher else None,
    )
    return facts, allow


def render(body: str, allow: QAAllowances) -> str:
    out = body
    for ph, url in PLACEHOLDER_URLS.items():
        out = out.replace(ph, url)
    if allow.voucher_code:
        out = out.replace(VOUCHER_PLACEHOLDER, allow.voucher_code)
    return out


def unrender(body: str, allow: QAAllowances) -> str:
    """Turn a rendered (possibly human-edited) email back into placeholder form for QA."""
    out = body
    for ph, url in sorted(PLACEHOLDER_URLS.items(), key=lambda kv: -len(kv[1])):
        out = out.replace(url, ph)
    if allow.voucher_code:
        out = out.replace(allow.voucher_code, VOUCHER_PLACEHOLDER)
    return out


# --------------------------------------------------------------------------- templates


def template_email(facts: CustomerFacts) -> tuple[str, str]:
    return _template_pt(facts) if facts.locale == "pt-BR" else _template_en(facts)


def _template_en(f: CustomerFacts) -> tuple[str, str]:
    multi = len(f.orders) > 1
    subject = (
        f"Update on your {len(f.orders)} orders: new delivery information"
        if multi
        else f"Update on your order {f.orders[0].order_ref}: new delivery information"
    )
    already_late = any(o.already_late_before_disruption for o in f.orders)
    opening = (
        f"We're sorry: your order{'s are' if multi else ' is'} running behind schedule, and {f.cause} is now adding "
        "to the delay."
        if already_late
        else f"We're sorry: {f.cause} is affecting the delivery of your order{'s' if multi else ''}."
    )
    parts = [
        f"Hi {f.first_name},",
        f"{opening} Here is what this means for you and what we are doing about it.",
    ]
    for o in f.orders:
        block = [f"Order {o.order_ref} (placed {o.purchase_date}): {', '.join(o.items)}. Originally promised by {o.promised_date}."]
        if o.already_late_before_disruption:
            block.append("This order was already running behind the promised date, and we apologise for that too.")
        block.append(o.resolution_summary)
        if o.cancelled_items and o.resolution != "CANCEL_REFUND":
            block.append(f"Because they are perishable, we cancelled these items: {', '.join(o.cancelled_items)}.")
        if o.split_shipment and o.resolution != "CANCEL_REFUND":
            block.append("Any items that are not affected will be shipped right away.")
        comp: list[str] = []
        if o.freight_refund:
            comp.append(f"we are refunding the freight you paid ({o.freight_refund})")
        if o.voucher_amount:
            comp.append(
                f"we are giving you a voucher of {o.voucher_amount} ({o.voucher_percent} of your items) with code "
                f"{VOUCHER_PLACEHOLDER}, valid for {VOUCHER_VALID_DAYS} days"
            )
        if comp:
            block.append("To make up for the delay, " + " and ".join(comp) + ".")
        if o.refund_total and o.resolution == "CANCEL_REFUND":
            block.append(f"You will receive a full refund of {o.refund_total} to your original payment method.")
        elif o.refund_total and o.cancelled_items:
            block.append(f"In total, {o.refund_total} will be refunded to your original payment method.")
        if o.offer_cancellation:
            block.append("If you would rather not wait, you can cancel at no cost here: [[CANCEL_LINK]].")
        if o.personal_follow_up:
            block.append("A member of our care team will also contact you personally.")
        parts.append(" ".join(block))
    parts.append(
        "You can follow your order at [[TRACK_LINK]]. If you have any questions, we're here to help: [[SUPPORT_LINK]]."
    )
    parts.append("Thank you for your patience and understanding.\n\nWarm regards,\nThe Customer Care Team")
    return subject, "\n\n".join(parts)


def _template_pt(f: CustomerFacts) -> tuple[str, str]:
    multi = len(f.orders) > 1
    subject = (
        f"Atualização sobre seus {len(f.orders)} pedidos: nova previsão de entrega"
        if multi
        else f"Atualização sobre seu pedido {f.orders[0].order_ref}: nova previsão de entrega"
    )
    already_late = any(o.already_late_before_disruption for o in f.orders)
    opening = (
        f"Pedimos desculpas: {'seus pedidos estão' if multi else 'seu pedido está'} atrasado{'s' if multi else ''}, "
        f"e {f.cause} está aumentando o atraso."
        if already_late
        else f"Pedimos desculpas: {f.cause} está afetando a entrega do seu pedido{'s' if multi else ''}."
    )
    parts = [
        f"Olá, {f.first_name},",
        f"{opening} Veja o que isso significa para você e o que estamos fazendo.",
    ]
    for o in f.orders:
        block = [f"Pedido {o.order_ref} (feito em {o.purchase_date}): {', '.join(o.items)}. Prazo original: {o.promised_date}."]
        if o.already_late_before_disruption:
            block.append("Este pedido já estava atrasado em relação ao prazo prometido, e pedimos desculpas também por isso.")
        block.append(o.resolution_summary)
        if o.cancelled_items and o.resolution != "CANCEL_REFUND":
            block.append(f"Por serem perecíveis, cancelamos estes itens: {', '.join(o.cancelled_items)}.")
        if o.split_shipment and o.resolution != "CANCEL_REFUND":
            block.append("Os itens não afetados serão enviados imediatamente.")
        comp: list[str] = []
        if o.freight_refund:
            comp.append(f"vamos reembolsar o frete pago ({o.freight_refund})")
        if o.voucher_amount:
            comp.append(
                f"oferecemos um cupom de {o.voucher_amount} ({o.voucher_percent} do valor dos itens) com o código "
                f"{VOUCHER_PLACEHOLDER}, válido por {VOUCHER_VALID_DAYS} dias"
            )
        if comp:
            block.append("Para compensar o atraso, " + " e ".join(comp) + ".")
        if o.refund_total and o.resolution == "CANCEL_REFUND":
            block.append(f"Você receberá o reembolso integral de {o.refund_total} no meio de pagamento original.")
        elif o.refund_total and o.cancelled_items:
            block.append(f"No total, {o.refund_total} serão reembolsados no meio de pagamento original.")
        if o.offer_cancellation:
            block.append("Se preferir não esperar, você pode cancelar sem custo aqui: [[CANCEL_LINK]].")
        if o.personal_follow_up:
            block.append("Uma pessoa da nossa equipe de atendimento também entrará em contato com você.")
        parts.append(" ".join(block))
    parts.append(
        "Você pode acompanhar seu pedido em [[TRACK_LINK]]. Se tiver dúvidas, estamos aqui para ajudar: [[SUPPORT_LINK]]."
    )
    parts.append("Obrigado pela paciência e compreensão.\n\nAtenciosamente,\nEquipe de Atendimento ao Cliente")
    return subject, "\n\n".join(parts)
