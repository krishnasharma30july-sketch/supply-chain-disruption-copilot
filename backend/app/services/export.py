"""Exports: Markdown impact report and CSV of order decisions."""

from __future__ import annotations

import csv
import io

from app.services.reporting import ACTION_LABELS

ORDER_CSV_COLUMNS = [
    "order_id", "customer_unique_id", "stage", "origin_states", "dest_state", "categories", "handling_profile",
    "value_brl", "billable_weight_kg", "hold_days", "days_late", "resolution_days_late", "p_breach",
    "severity_score", "severity_tier", "action", "offer_cancellation", "needs_review", "decided_by", "est_cost_brl",
    "sla_ts", "projected_eta", "resolution_eta",
]


def _fmt(v) -> str:
    if isinstance(v, list):
        return "|".join(str(x) for x in v)
    if hasattr(v, "isoformat"):
        return v.isoformat(sep=" ", timespec="minutes")
    if isinstance(v, float):
        return f"{v:.2f}"
    return "" if v is None else str(v)


def orders_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(ORDER_CSV_COLUMNS)
    for r in rows:
        w.writerow([_fmt(r.get(c)) for c in ORDER_CSV_COLUMNS])
    return buf.getvalue()


def report_markdown(
    run: dict,
    summary: dict | None,
    orders: list[dict],
    comms: list[dict],
    assumptions: list[dict],
    *,
    top_orders: int = 25,
    sample_emails: int = 2,
) -> str:
    spec = run.get("spec") or {}
    out: list[str] = [f"# Impact report: {run['title']}", ""]
    out.append(f"- **Run:** `{run['id']}` | status **{run['status']}** | LLM mode: {run.get('llm_mode')}")
    if run.get("raw_query"):
        out.append(f"- **Scenario:** {run['raw_query']}")
    if spec:
        def geo(g: dict) -> str:
            parts = []
            if g.get("states"):
                parts.append("states " + ", ".join(g["states"]))
            if g.get("cities"):
                parts.append("cities " + ", ".join(c["label"] for c in g["cities"]))
            if g.get("radius"):
                parts.append(f"within {g['radius']['km']:g} km of {g['radius']['center']['label']}")
            return "; ".join(parts) or "-"

        out.append(
            f"- **Interpretation ({spec.get('source')}, confidence {spec.get('confidence')}):** "
            f"{spec.get('disruption_type')} | origin: {geo(spec.get('origin', {}))} | destination: {geo(spec.get('destination', {}))}"
            f"{' | nationwide' if spec.get('nationwide') else ''} | legs: {', '.join(spec.get('legs', []))} | "
            f"{spec.get('duration_days')} days from {str(spec.get('start_at'))[:16]} + {spec.get('recovery_days')} recovery days"
        )
        for a in spec.get("ambiguities", []):
            out.append(f"- **Ambiguity:** '{a['mention']}' interpreted as {a['options'][a['chosen']]['label']} ({a['reason']})")
    out.append("")
    if summary:
        s = summary["stats"]
        sc = s["scope"]
        out += [f"## {summary['headline']}", "", summary["narrative"], ""]
        out += ["## Key figures", "", "| Metric | Value |", "|---|---|"]
        rows = [
            ("Orders in scope", sc["orders_in_scope"]), ("Orders delayed", sc["orders_delayed"]),
            ("Orders missing the promised date", sc["orders_breaching"]),
            ("...of which only because of this disruption", sc["orders_newly_breaching"]),
            ("Customers affected", sc["customers_delayed"]), ("Sellers affected", sc["sellers_delayed"]),
            ("SKUs affected", sc["skus_delayed"]), ("Order value delayed", f"R$ {sc['gmv_delayed_brl']:,.2f}"),
            ("Billable weight delayed", f"{sc['billable_kg_delayed']:,.1f} kg"),
            ("Average hold", f"{sc['avg_hold_days']} days"), ("Bulky orders delayed", sc["bulky_orders_delayed"]),
            ("Severity (critical/high/medium/low)", "/".join(str(s["severity"].get(t, 0)) for t in ("critical", "high", "medium", "low"))),
            ("Estimated mitigation cost", f"R$ {s['cost']['total_brl']:,.2f}"),
        ]
        out += [f"| {k} | {v} |" for k, v in rows]
        out += ["", "### Decisions", "", "| Decision | Rule code | Orders |", "|---|---|---|"]
        out += [f"| {ACTION_LABELS.get(k, k)} | `{k}` | {v} |" for k, v in s["actions"].items()]
        out += ["", "### Recommendations", ""] + [f"- {r}" for r in summary["recommendations"]]
        if s["top_categories"]:
            out += ["", "### Most affected categories", "", "| Category | Orders | Items | Value (R$) |", "|---|---|---|---|"]
            out += [f"| {c['category']} | {c['orders']} | {c['items']} | {c['value_brl']:,.2f} |" for c in s["top_categories"]]
        if s["top_sellers"]:
            out += ["", "### Most exposed sellers", "", "| Seller | Location | Orders | Value (R$) |", "|---|---|---|---|"]
            out += [f"| {t['seller_id'][:8]} | {t['city'].title()}/{t['state']} | {t['orders']} | {t['value_brl']:,.2f} |" for t in s["top_sellers"]]
        retrieval = summary.get("retrieval") or {}
        if retrieval.get("queries"):
            out += ["", "### Retrieval evidence (hybrid search)", ""]
            for q in retrieval["queries"]:
                hits = ", ".join(f"{h['title']} ({h['score']:.4f})" for h in q["hits"][:3]) or "no hits"
                out.append(f"- **{q['purpose']}**: {hits}")
    delayed = [o for o in orders if o.get("affected")]
    if delayed:
        out += ["", f"## Top {min(top_orders, len(delayed))} orders by severity", "",
                "| Order | Stage | Lane | Category | kg | Days late | Severity | Action |", "|---|---|---|---|---|---|---|---|"]
        for o in delayed[:top_orders]:
            out.append(
                f"| {o['order_id'][:8].upper()} | {o['stage']} | {','.join(o['origin_states'])}->{o['dest_state']} | "
                f"{', '.join(c.replace('_', ' ') for c in o['categories'][:2])} | {o['billable_weight_kg']:.1f} | "
                f"{o['days_late']:.1f} | {o['severity_score']:.0f} ({o['severity_tier']}) | "
                f"{ACTION_LABELS.get(o['action'], o['action'])} |"
            )
    if comms:
        out += ["", "## Sample customer emails", ""]
        for c in comms[:sample_emails]:
            qa = c.get("qa") or {}
            out += [
                f"### To {c['first_name']} (orders {', '.join(o[:8].upper() for o in c['order_ids'])}): "
                f"{c['generator']}, QA {'passed' if qa.get('passed') else 'failed'}",
                "", f"**Subject:** {c['subject']}", "", "```text", c["body"], "```", "",
            ]
    if assumptions:
        out += ["", "## Assumptions", ""] + [f"- **{a['id']} {a['title']}:** {a['statement']}" for a in assumptions]
    return "\n".join(out) + "\n"
