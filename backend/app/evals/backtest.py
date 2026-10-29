"""Backtest against real events in the Olist history.

For each event the snapshot is rebuilt in memory at the event's start (future masked), the event is replayed through
the same deterministic engine, and predictions are scored against the deliveries that actually happened.

Events (the specs are this module's modelling choices, documented in the report):
- Brazilian truckers' strike, 21 May 2018, ~10 days, nationwide road freight (the reference event).
- Black Friday 2017 (24 Nov), a nationwide capacity crunch for sellers and carriers.
- Carnival 2018 (10-14 Feb), a nationwide holiday with reduced pickups and deliveries.
plus a calm control period with no disruption.

Besides precision / recall / ETA error, the breach probabilities are scored with the Brier score and a reliability
table, and the partial-impact factor (A8) is swept per event; the default only changes when every event agrees.
This module is the ONLY runtime reader of unmasked actuals.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime

import polars as pl

from app.core.config import get_settings
from app.core.policy_config import get_assumptions
from app.domain.models import DisruptionSpec, LaneStat, LineRecord, SellerStat
from app.etl.build import build_frames, load_raw
from app.etl.download import ensure_raw
from app.services.eta import LaneBook
from app.services.impact import EngineContext, order_impact
from app.services.scope import ConceptFilter, match_lines

ALL_LEGS = ["first_mile", "linehaul", "last_mile"]


@dataclass(frozen=True)
class Event:
    key: str
    title: str
    disruption_type: str
    start: datetime
    days: float
    note: str


EVENTS = [
    Event("strike", "Nationwide truckers' strike", "strike", datetime(2018, 5, 21), 10.0,
          "Road freight stopped nationwide for about 10 days (the reference event)."),
    Event("black_friday", "Black Friday capacity crunch", "capacity", datetime(2017, 11, 24), 5.0,
          "Order volume spiked several-fold; modelled as a 5-day nationwide capacity disruption."),
    Event("carnival", "Carnival holiday", "other", datetime(2018, 2, 10), 5.0,
          "Saturday to Ash Wednesday; modelled as a 5-day nationwide slowdown of pickups and deliveries."),
]
STRIKE = EVENTS[0]
STRIKE_START = STRIKE.start
STRIKE_DAYS = STRIKE.days
CONTROL_AS_OF = datetime(2018, 4, 16)  # a normal period 5 weeks before the strike, same weekday

FACTORS = [1.0, 0.75, 0.5, 0.25]
BINS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0000001]


def _engine_inputs(f: dict, as_of: datetime):
    params = get_assumptions().params
    orders = f["orders"].filter(pl.col("stage") != "delivered")
    items = f["items"].join(orders.select("order_id"), on="order_id", how="semi")
    cats = f["categories"].select(
        pl.col("name_pt").alias("category_pt"), pl.col("name_en").alias("category_en"), "product_class", "class_confidence"
    )
    prod = f["products"].select("product_id", "category_pt", "weight_g", "length_cm", "height_cm", "width_cm",
                                "billable_weight_kg", "handling_profile", "imputed").join(cats, on="category_pt", how="left")
    sellers = f["sellers"].select("seller_id", pl.col("city_norm").alias("seller_city"), pl.col("state").alias("seller_state"),
                                  pl.col("lat").alias("seller_lat"), pl.col("lng").alias("seller_lng"))
    customers = f["customers"].select("customer_id", "customer_unique_id", pl.col("city_norm").alias("cust_city"),
                                      pl.col("state").alias("cust_state"), pl.col("lat").alias("cust_lat"), pl.col("lng").alias("cust_lng"))
    rows = (
        items.join(orders.select("order_id", "customer_id", "stage", "purchase_ts", "approved_ts", "carrier_ts",
                                 "estimated_delivery_ts"), on="order_id")
        .join(prod, on="product_id", how="left").join(sellers, on="seller_id", how="left")
        .join(customers, on="customer_id", how="left")
        .sort("order_id", "order_item_id")
        .to_dicts()
    )
    lines: list[LineRecord] = []
    by_order: dict[str, list[LineRecord]] = {}
    for r in rows:
        rec = LineRecord(
            order_id=r["order_id"], order_item_id=r["order_item_id"], product_id=r["product_id"], seller_id=r["seller_id"],
            customer_id=r["customer_id"], customer_unique_id=r["customer_unique_id"], stage=r["stage"],
            purchase_ts=r["purchase_ts"], approved_ts=r["approved_ts"], carrier_ts=r["carrier_ts"],
            sla_ts=r["estimated_delivery_ts"], shipping_limit_ts=r["shipping_limit_ts"], price=float(r["price"]),
            freight=float(r["freight_value"]), seller_city=r["seller_city"] or "", seller_state=r["seller_state"],
            seller_lat=r["seller_lat"], seller_lng=r["seller_lng"], cust_city=r["cust_city"] or "",
            cust_state=r["cust_state"], cust_lat=r["cust_lat"], cust_lng=r["cust_lng"], category_pt=r["category_pt"],
            category_en=r["category_en"] or "unknown", product_class=r["product_class"] or "STANDARD",
            class_confidence=float(r["class_confidence"] or 0.3), handling_profile=r["handling_profile"],
            billable_weight_kg=float(r["billable_weight_kg"]), weight_kg=float(r["weight_g"]) / 1000,
            length_cm=float(r["length_cm"]), height_cm=float(r["height_cm"]), width_cm=float(r["width_cm"]),
            imputed=bool(r["imputed"]),
        )
        lines.append(rec)
        by_order.setdefault(rec.order_id, []).append(rec)
    lanes = LaneBook([LaneStat(**r) for r in f["lanes"]], params.lane_min_samples)
    sstats = {
        r["seller_id"]: SellerStat(seller_id=r["seller_id"], orders_n=r["orders_n"], late_handover_rate=r["late_handover_rate"],
                                   late_delivery_rate=r["late_delivery_rate"], median_handling_days=r["median_handling_days"])
        for r in f["seller_stats"].to_dicts()
    }
    ctx = EngineContext(as_of=as_of, lanes=lanes, seller_stats=sstats, global_handling_days=f["global_handling_days"],
                        global_approval_days=f["global_approval_days"], params=params)
    return lines, by_order, ctx


def _prf(pred: list[bool], actual: list[bool]) -> dict:
    tp = sum(p and a for p, a in zip(pred, actual, strict=True))
    fp = sum(p and not a for p, a in zip(pred, actual, strict=True))
    fn = sum((not p) and a for p, a in zip(pred, actual, strict=True))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"predicted_late": tp + fp, "precision": precision, "recall": recall, "f1": f1}


def _brier(probs: list[float], actual: list[bool]) -> float:
    return sum((p - float(a)) ** 2 for p, a in zip(probs, actual, strict=True)) / len(probs) if probs else 0.0


def _reliability(probs: list[float], actual: list[bool]) -> list[dict]:
    out = []
    for lo, hi in zip(BINS, BINS[1:], strict=False):
        idx = [i for i, p in enumerate(probs) if lo <= p < hi]
        if idx:
            out.append({"bin": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": len(idx),
                        "predicted": sum(probs[i] for i in idx) / len(idx),
                        "observed": sum(actual[i] for i in idx) / len(idx)})
    return out


def _logit(p: float) -> float:
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def _fit_platt(probs: list[float], actual: list[bool], iters: int = 50) -> tuple[float, float]:
    """Platt scaling p' = sigmoid(a * logit(p) + b), fitted by damped Newton steps on the log-loss."""
    a, b = 1.0, 0.0
    xs = [_logit(p) for p in probs]
    for _ in range(iters):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in zip(xs, actual, strict=True):
            p = _sigmoid(a * x + b)
            w, r = p * (1 - p), p - float(y)
            g0, g1 = g0 + r * x, g1 + r
            h00, h01, h11 = h00 + w * x * x, h01 + w * x, h11 + w
        det = h00 * h11 - h01 * h01
        if det <= 1e-12:
            break
        da, db = (h11 * g0 - h01 * g1) / det, (h00 * g1 - h01 * g0) / det
        norm = math.hypot(da, db)
        if norm > 1.0:  # damp large steps
            da, db = da / norm, db / norm
        a, b = a - da, b - db
        if norm < 1e-6:
            break
    return a, b


def _platt(probs: list[float], ab: tuple[float, float]) -> list[float]:
    return [_sigmoid(ab[0] * _logit(p) + ab[1]) for p in probs]


def _spec(event: Event | None, as_of: datetime) -> DisruptionSpec:
    if event is None:
        return DisruptionSpec(title="No disruption (control)", legs=["first_mile"], nationwide=True, start_at=as_of,
                              duration_days=1, source="user")
    return DisruptionSpec(
        title=event.title, disruption_type=event.disruption_type, legs=ALL_LEGS, nationwide=True, start_at=as_of,
        duration_days=event.days,
        recovery_days=float(math.ceil(get_assumptions().params.recovery_factor * event.days)), source="user",
    )


def _evaluate(raw: dict, as_of: datetime, event: Event | None) -> dict:
    """Score the baseline model and, for an event, the disruption-aware model at each partial-impact factor.
    The snapshot is built once per date; only the engine parameter changes between factors."""
    f = build_frames(raw, as_of, corpus_size=1_000_000)
    lines, by_order, ctx = _engine_inputs(f, as_of)
    actual = {r["order_id"]: r["delivered_ts"] for r in f["actuals"].to_dicts()}
    spec = _spec(event, as_of)
    matched = match_lines(spec, lines, ConceptFilter()) if event is not None else {}
    factors = FACTORS if event is not None else [1.0]

    scored = [(oid, ls, actual[oid]) for oid, ls in by_order.items() if actual.get(oid) is not None]
    y_true = [d.date() > ls[0].sla_ts.date() for _, ls, d in scored]
    n = len(scored)
    by_factor: dict[float, dict] = {}
    baseline: dict = {}
    for factor in factors:
        fctx = replace(ctx, params=ctx.params.model_copy(update={"partial_impact_factor": factor}))
        p_base, p_dis, err_base, err_dis, pr_base, pr_dis = [], [], [], [], [], []
        for oid, ls, delivered in scored:
            imp = order_impact(ls, {m.line.order_item_id: m for m in matched.get(oid, [])}, spec, fctx)
            sla = imp.sla_ts.date()
            p_base.append(imp.baseline_eta.date() > sla)
            p_dis.append(imp.projected_eta.date() > sla)
            err_base.append(abs((imp.baseline_eta - delivered).total_seconds()) / 86400)
            err_dis.append(abs((imp.projected_eta - delivered).total_seconds()) / 86400)
            pr_base.append(imp.p_breach_baseline)
            pr_dis.append(imp.p_breach)
        by_factor[factor] = {**_prf(p_dis, y_true), "mae_days": sum(err_dis) / n if n else 0.0,
                             "brier": _brier(pr_dis, y_true), "probs": pr_dis}
        if factor == 1.0:
            reliability = _reliability(pr_dis, y_true)
        if not baseline:
            baseline = {**_prf(p_base, y_true), "mae_days": sum(err_base) / n if n else 0.0,
                        "brier": _brier(pr_base, y_true)}
            reliability_base = _reliability(pr_base, y_true)
            base_probs = pr_base
    return {
        "event": event, "as_of": as_of, "orders": n, "actual_late": sum(y_true), "y_true": y_true, "base_probs": base_probs,
        "actual_late_rate": sum(y_true) / n if n else 0.0, "baseline": baseline, "disruption": by_factor[1.0],
        "by_factor": by_factor, "reliability": reliability, "reliability_baseline": reliability_base,
    }


def _row(name: str, m: dict) -> str:
    return (f"| {name} | {m['predicted_late']} | {m['precision']:.1%} | {m['recall']:.1%} | {m['f1']:.2f} | "
            f"{m['mae_days']:.1f} | {m['brier']:.3f} |")


HEAD = ["| Model | Predicted late | Precision | Recall | F1 | ETA MAE (days) | Brier |", "|---|---|---|---|---|---|---|"]


def run_backtest() -> str:
    raw = load_raw(ensure_raw(get_settings().data_dir))
    results = [_evaluate(raw, e.start, e) for e in EVENTS]
    control = _evaluate(raw, CONTROL_AS_OF, None)
    strike = results[0]
    # Leave-one-event-out recalibration: fit on the other events, score on the held-out one.
    for i, r in enumerate(results):
        others = [o for j, o in enumerate(results) if j != i]
        ab = _fit_platt([p for o in others for p in o["disruption"]["probs"]], [y for o in others for y in o["y_true"]])
        r["calibrated"] = _platt(r["disruption"]["probs"], ab)
        r["brier_calibrated"] = _brier(r["calibrated"], r["y_true"])
    full_ab = _fit_platt([p for r in results for p in r["disruption"]["probs"]], [y for r in results for y in r["y_true"]])

    out = [
        "# Backtest against real events",
        "",
        "For each event the snapshot was rebuilt as of the event's first day with all later timestamps masked, the event "
        "was replayed through the same engine the app uses, and predictions were compared with the deliveries that "
        "actually happened. Only orders in flight at that moment that were eventually delivered are scored. Brier is "
        "the mean squared error of the breach probability (lower is better; 0.25 is a coin flip).",
        "",
    ]
    for r in results:
        e: Event = r["event"]
        out += [
            f"## {e.title} ({e.start:%Y-%m-%d}, {e.days:g} days + recovery)",
            "",
            e.note,
            "",
            f"- In-flight orders scored: **{r['orders']}**; actually delivered late: **{r['actual_late']}** "
            f"({r['actual_late_rate']:.1%})",
            "",
            *HEAD,
            _row("Baseline ETA (no disruption awareness)", r["baseline"]),
            _row("Disruption-aware ETA (this system, factor 1.0)", r["disruption"]),
            "",
            "Partial-impact factor sweep (A8: share of the window a held shipment actually loses):",
            "",
            *[h.replace("Model", "Factor") for h in HEAD],
            *[_row(f"{fac:g}", m) for fac, m in r["by_factor"].items()],
            "",
        ]
    out += [
        f"## Control period ({CONTROL_AS_OF:%Y-%m-%d}, no disruption)",
        "",
        f"- In-flight orders scored: **{control['orders']}**; actually late: **{control['actual_late']}** "
        f"({control['actual_late_rate']:.1%})",
        "",
        *HEAD,
        _row("Baseline ETA", control["baseline"]),
        "",
        "## Calibration of breach probabilities (truckers' strike, factor 1.0)",
        "",
        "| Predicted p(breach) | Orders | Mean predicted | Observed late rate |",
        "|---|---|---|---|",
        *[f"| {b['bin']} | {b['n']} | {b['predicted']:.0%} | {b['observed']:.0%} |" for b in strike["reliability"]],
        "",
        "Same orders, baseline model (no disruption awareness):",
        "",
        "| Predicted p(breach) | Orders | Mean predicted | Observed late rate |",
        "|---|---|---|---|",
        *[f"| {b['bin']} | {b['n']} | {b['predicted']:.0%} | {b['observed']:.0%} |" for b in strike["reliability_baseline"]],
        "",
        "## Recalibrated breach probabilities (leave-one-event-out Platt scaling)",
        "",
        "The engine's p(breach) answers \"if the scenario happens exactly as described\" (every in-scope shipment held "
        "for the whole window). Real events hit less uniformly, so as a forecast it is over-confident. Platt scaling "
        "`p' = sigmoid(a * logit(p) + b)` was fitted on two events and scored on the third:",
        "",
        "| Held-out event | Brier baseline | Brier raw (this system) | Brier recalibrated |",
        "|---|---|---|---|",
        *[f"| {r['event'].title} | {r['baseline']['brier']:.3f} | {r['disruption']['brier']:.3f} | "
          f"{r['brier_calibrated']:.3f} |" for r in results],
        "",
        "Truckers' strike after recalibration (fitted on the other two events):",
        "",
        "| Predicted p(breach) | Orders | Mean predicted | Observed late rate |",
        "|---|---|---|---|",
        *[f"| {b['bin']} | {b['n']} | {b['predicted']:.0%} | {b['observed']:.0%} |"
          for b in _reliability(strike["calibrated"], strike["y_true"])],
        "",
        f"Fitted on all three events: a = {full_ab[0]:.2f}, b = {full_ab[1]:.2f}.",
        "",
        "## Summary across events",
        "",
        "| Event | Recall baseline -> ours | F1 baseline -> ours | Brier baseline -> raw -> recalibrated | Best-F1 factor |",
        "|---|---|---|---|---|",
        *[
            f"| {r['event'].title} | {r['baseline']['recall']:.0%} -> {r['disruption']['recall']:.0%} | "
            f"{r['baseline']['f1']:.2f} -> {r['disruption']['f1']:.2f} | "
            f"{r['baseline']['brier']:.3f} -> {r['disruption']['brier']:.3f} -> {r['brier_calibrated']:.3f} | "
            f"{_best_factor(r):g} |"
            for r in results
        ],
        "",
        "## Reading the results",
        "",
        *_findings(results, control),
    ]
    return "\n".join(out) + "\n"


def _best_factor(r: dict) -> float:
    return max(r["by_factor"].items(), key=lambda kv: kv[1]["f1"])[0]


def _findings(results: list[dict], control: dict) -> list[str]:
    """Data-driven conclusions: every sentence is derived from the numbers above."""
    strike = results[0]
    b, d = strike["baseline"], strike["disruption"]
    out: list[str] = []
    out.append(
        f"- Truckers' strike: recall (truly late orders flagged proactively) moves from {b['recall']:.0%} to "
        f"{d['recall']:.0%} and F1 from {b['f1']:.2f} to {d['f1']:.2f}: the disruption-aware model "
        + ("finds far more of the orders that really ended up late." if d["recall"] > b["recall"] else "does not improve recall.")
    )
    out.append(
        f"- Precision moves from {b['precision']:.0%} to {d['precision']:.0%}: "
        f"{d['predicted_late'] - round(d['precision'] * d['predicted_late'])} of the {d['predicted_late']} flagged orders "
        "still arrived on time. That is the cost of over-notifying, which is why on-time-but-shifted orders get a "
        "heads-up rather than compensation (rule R1)."
    )
    improved = [r for r in results if r["disruption"]["f1"] > r["baseline"]["f1"]]
    out.append(
        f"- F1 improves over the baseline in {len(improved)} of {len(results)} events "
        f"({', '.join(r['event'].title for r in improved) or 'none'})."
        + ("" if len(improved) == len(results) else
           " Where it does not, the event's real effect was smaller than a full nationwide hold, which is what the "
           "partial-impact factor and an explicit delay in the scenario text are for.")
    )
    brier_better = [r for r in results if r["disruption"]["brier"] < r["baseline"]["brier"]]
    cal_better = [r for r in results if r["brier_calibrated"] < r["disruption"]["brier"]]
    cal_vs_base = [r for r in results if r["brier_calibrated"] <= r["baseline"]["brier"]]
    out.append(
        f"- Breach probabilities: the raw scenario-conditional probability beats the baseline's Brier in "
        f"{len(brier_better)} of {len(results)} events, because it assumes the full hold. Out-of-sample "
        f"recalibration improves it in {len(cal_better)} of {len(results)} events and matches or beats the baseline in "
        f"{len(cal_vs_base)}. The value of disruption awareness is in ranking and recall (who to contact first); the "
        "probability shown in the app is the scenario-conditional one, and these numbers show how to read it as a "
        "real-world forecast."
    )
    best = {_best_factor(r) for r in results}
    if len(best) == 1 and best != {1.0}:
        out.append(
            f"- Calibration: every event has its best F1 at factor {next(iter(best)):g}, a consistent signal to lower "
            "`partial_impact_factor` from its conservative default of 1.0."
        )
    else:
        out.append(
            "- Calibration: the best-F1 factor differs across events ("
            + ", ".join(f"{r['event'].title} {_best_factor(r):g}" for r in results)
            + "), so the default stays at 1.0 (conservative for proactive communication). It is a configurable "
            "assumption (`partial_impact_factor`) to re-fit per disruption type as more events are observed."
        )
    diff = strike["actual_late_rate"] - control["actual_late_rate"]
    out.append(
        f"- The real late-delivery rate was {strike['actual_late_rate']:.1%} during the strike versus "
        f"{control['actual_late_rate']:.1%} in the control period"
        + (": the event had a measurable effect." if diff > 0 else
           ", so the control period was not calmer. Olist's early-2018 lateness was already elevated, and promised "
           "dates carry large buffers that absorbed much of the strike.")
    )
    return out
