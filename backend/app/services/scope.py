"""Deterministic scope matching: which order lines does a disruption touch, through which legs, and why."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.models import DisruptionSpec, GeoScope, Leg, LineRecord
from app.services.geo import haversine_km


@dataclass
class ConceptFilter:
    """Product filter resolved from free-text terms ("furniture", "bulky", "perishable")."""

    categories: dict[str, str] = field(default_factory=dict)  # name_pt -> name_en
    classes: set[str] = field(default_factory=set)
    heavy_bulky: bool = False
    heavy_min_kg: float = 15.0

    @property
    def active(self) -> bool:
        return bool(self.categories or self.classes or self.heavy_bulky)

    def match(self, line: LineRecord) -> str | None:
        if line.category_pt and line.category_pt in self.categories:
            return f"CATEGORY:{line.category_en}"
        if line.product_class in self.classes:
            return f"CLASS:{line.product_class}"
        if self.heavy_bulky and (line.handling_profile == "BULKY" or line.billable_weight_kg >= self.heavy_min_kg):
            return "HEAVY_BULKY"
        return None


def geo_match(geo: GeoScope, city: str, state: str, lat: float | None, lng: float | None) -> list[str]:
    reasons: list[str] = []
    if state in geo.states:
        reasons.append(f"STATE:{state}")
    for c in geo.cities:
        if c.name == city and c.state == state:
            reasons.append(f"CITY:{c.label}")
            break
    if geo.radius is not None and lat is not None and lng is not None:
        dist = haversine_km(geo.radius.lat, geo.radius.lng, lat, lng)
        if dist <= geo.radius.km:
            reasons.append(f"RADIUS:{dist:.0f}km<={geo.radius.km:g}km of {geo.radius.center.label}")
    return reasons


@dataclass
class LineMatch:
    line: LineRecord
    reasons: list[str]
    legs: set[Leg]
    origin_hit: bool = False
    dest_hit: bool = False
    # True when the disruption covers the whole corridor (nationwide, a specific lane, or no geography):
    # the entire linehaul is exposed. Otherwise only the part of the linehaul inside the region is.
    corridor: bool = False


def match_line(spec: DisruptionSpec, line: LineRecord, concepts: ConceptFilter) -> LineMatch | None:
    """Return the match (reasons + legs geographically exposed) or None when the line is out of scope."""
    reasons: list[str] = []
    if spec.seller_ids:
        if line.seller_id not in spec.seller_ids:
            return None
        reasons.append(f"SELLER:{line.seller_id[:8]}")
    if concepts.active:
        concept_reason = concepts.match(line)
        if concept_reason is None:
            return None
        reasons.append(concept_reason)

    origin_reasons = geo_match(spec.origin, line.seller_city, line.seller_state, line.seller_lat, line.seller_lng)
    dest_reasons = geo_match(spec.destination, line.cust_city, line.cust_state, line.cust_lat, line.cust_lng)

    if spec.nationwide:
        origin_hit = dest_hit = True
        reasons.append("NATIONWIDE")
    elif not spec.has_geo():
        # Scope is defined only by sellers and/or product concepts.
        origin_hit = dest_hit = True
    else:
        origin_hit = bool(origin_reasons)
        dest_hit = bool(dest_reasons)

    legs: set[Leg] = set()
    if spec.match_mode == "lane" and not spec.nationwide:
        if origin_hit and dest_hit:
            legs = set(spec.legs)
    else:
        if "first_mile" in spec.legs and origin_hit:
            legs.add("first_mile")
        if "linehaul" in spec.legs and (origin_hit or dest_hit):
            legs.add("linehaul")
        if "last_mile" in spec.legs and dest_hit:
            legs.add("last_mile")
    if not legs:
        return None
    reasons.extend(f"ORIGIN_{r}" for r in origin_reasons)
    reasons.extend(f"DEST_{r}" for r in dest_reasons)
    corridor = spec.nationwide or spec.match_mode == "lane" or not spec.has_geo()
    return LineMatch(line=line, reasons=reasons, legs=legs, origin_hit=origin_hit, dest_hit=dest_hit, corridor=corridor)


def match_lines(spec: DisruptionSpec, lines: list[LineRecord], concepts: ConceptFilter) -> dict[str, list[LineMatch]]:
    """Group matched lines by order id."""
    out: dict[str, list[LineMatch]] = {}
    for line in lines:
        m = match_line(spec, line, concepts)
        if m is not None:
            out.setdefault(line.order_id, []).append(m)
    return out
