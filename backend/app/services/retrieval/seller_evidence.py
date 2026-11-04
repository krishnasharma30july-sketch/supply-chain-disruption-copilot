"""Seller evidence for a run: a scenario-aware search query, and a ranking that blends search relevance with the exact
exposure of each seller (affected orders and value).

Why: "Most exposed impacted sellers" should be ordered by exposure. A text search alone ranks by wording, so for a
destination-side scenario (a courier strike in Rio) it prefers sellers *located* in Rio, although most affected sellers
only have a parcel or two going there. Retrieval proposes and explains; the exact numbers decide the order."""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.models import DisruptionSpec
from app.services.geo import STATE_NAMES
from app.services.retrieval.hybrid import SearchFilters, SearchHit, hybrid_search


@dataclass(frozen=True)
class SellerExposure:
    orders: int
    items: int
    value_brl: float

    def as_dict(self) -> dict:
        return {"orders": self.orders, "items": self.items, "value_brl": round(self.value_brl, 2)}


def _where(states: list[str], cities: list) -> str:
    named = [f"{STATE_NAMES.get(uf, uf)} ({uf})" for uf in states]
    named += [c.label for c in cities]
    return ", ".join(named)


def seller_query(spec: DisruptionSpec) -> str:
    """Words that match how a seller document describes the scenario: where its parcels go (destination side) or where it
    is located (origin side). Seller documents carry both ("Parcels in the pipeline now go to ...", "located in ...")."""
    origin_side = any(leg in spec.legs for leg in ("first_mile", "linehaul"))
    dest_side = "last_mile" in spec.legs
    lane = spec.match_mode == "lane"
    parts: list[str] = []
    if spec.nationwide:
        parts.append("Sellers shipping parcels all over Brazil")
    dest = _where(spec.destination.states, spec.destination.cities)
    orig = _where(spec.origin.states, spec.origin.cities)
    dest_phrase = f"Parcels in the pipeline now go to {dest}" if dest and (dest_side or lane) else None
    if dest_phrase:
        parts.append(dest_phrase)
    # An origin-side scenario already names its origin in the title ("... leaving Sao Paulo ..."); adding "Seller located
    # in ..." made the search worse in the evaluation (São Paulo sellers, top 25: 96% with the title alone, 84% with it).
    # The phrase is only added next to a destination phrase, where it carries the second end of a lane.
    if dest_phrase and orig and (origin_side or lane):
        parts.append(f"Seller located in {orig}")
    if spec.category_terms:
        parts.append("Sells " + ", ".join(spec.category_terms))
    # The scenario title goes first and keeps its own wording; the destination phrase adds what a courier strike needs
    # (Rio sellers, top 10: 40% with the title alone, 70% with the phrase; measured in the eval).
    return ". ".join([spec.title, *parts])


def exposure_by_seller(matched: dict) -> dict[str, SellerExposure]:
    """Exact exposure per seller from the structured scope: distinct affected orders, items and value (price + freight)."""
    orders: dict[str, set[str]] = {}
    items: dict[str, int] = {}
    value: dict[str, float] = {}
    for ms in matched.values():
        for m in ms:
            sid = m.line.seller_id
            orders.setdefault(sid, set()).add(m.line.order_id)
            items[sid] = items.get(sid, 0) + 1
            value[sid] = value.get(sid, 0.0) + float(m.line.price) + float(m.line.freight)
    return {sid: SellerExposure(len(orders[sid]), items[sid], value[sid]) for sid in orders}


def _percentiles(order_keys: list) -> dict:
    """Map each key (already sorted best first) to a 0..1 score, 1 for the best."""
    n = len(order_keys)
    return {k: (1.0 if n == 1 else 1.0 - i / (n - 1)) for i, k in enumerate(order_keys)}


def exposure_scores(ids: list[str], exposure: dict[str, SellerExposure]) -> dict[str, float]:
    """0..1 score of how exposed each seller is: affected orders on a log scale (a seller with 25 affected orders clearly
    outranks one with 2, which a rank percentile would not show), with the value as a small tie-break. This follows the
    order of the Impact summary's "Most exposed sellers" table (affected orders, then value)."""
    orders = {sid: (exposure[sid].orders if sid in exposure else 0) for sid in ids}
    value = {sid: (exposure[sid].value_brl if sid in exposure else 0.0) for sid in ids}
    max_o, max_v = max(orders.values(), default=0), max(value.values(), default=0.0)
    out = {}
    for sid in ids:
        o = math.log1p(orders[sid]) / math.log1p(max_o) if max_o > 0 else 0.0
        v = value[sid] / max_v if max_v > 0 else 0.0
        out[sid] = (o + 0.01 * v) / 1.01
    return out


def blend_sellers(
    hits: list[SearchHit], exposure: dict[str, SellerExposure], weight: float, limit: int
) -> list[tuple[SearchHit, SellerExposure | None, float]]:
    """Order sellers by weight * exposure score + (1 - weight) * relevance percentile. Hits arrive best-match first.
    Returns (hit, exposure, blended score), best first."""
    if not hits:
        return []
    relevance = _percentiles([h.ref_id for h in hits])
    exposed = exposure_scores([h.ref_id for h in hits], exposure)
    scored = [(h, exposure.get(h.ref_id), weight * exposed[h.ref_id] + (1 - weight) * relevance[h.ref_id]) for h in hits]
    scored.sort(key=lambda t: (-t[2], t[0].ref_id))
    return scored[:limit]


async def retrieve_sellers(session, embedder, query: str, seller_ids: list[str]) -> list[SearchHit]:
    """Search ranking of ALL the impacted sellers. The search depth `k` is raised to the number of sellers: with the default
    of 200, a scope of 491 sellers would leave about half of them out of the pool, including some of the most exposed."""
    n = len(seller_ids)
    if n == 0:
        return []
    return await hybrid_search(
        session, embedder, query, SearchFilters(doc_types=["seller"], ref_ids=sorted(seller_ids)),
        limit=min(n, 1000), k=max(200, min(n, 1000)),
    )
