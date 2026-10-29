"""Ground a raw interpretation (from the LLM or the rule parser) into a validated DisruptionSpec.

Place names are resolved against the gazetteer; names that are both a state and a city (Sao Paulo,
Rio de Janeiro) become explicit ambiguities unless the text qualifies them (assumption A18).
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from app.core.policy_config import AssumptionParams
from app.domain.models import (
    LEGS,
    Ambiguity,
    AmbiguityOption,
    CityRef,
    DisruptionSpec,
    DisruptionType,
    GeoScope,
    Leg,
    RadiusRef,
    Role,
)
from app.services.geo import STATE_NAMES, Gazetteer, PlaceCandidate, normalize

SELLER_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class RawIntake(BaseModel):
    """Flat interpretation produced by the LLM (structured output) or the rule parser."""

    title: str = Field(description="Short title for the disruption, max 80 characters.")
    disruption_type: DisruptionType
    origin_places: list[str] = Field(description="Places freight LEAVES from (e.g. 'leaving Sao Paulo', 'from SP').")
    destination_places: list[str] = Field(description="Places freight is DELIVERED to (e.g. 'deliveries into Rio').")
    both_places: list[str] = Field(description="Places where the event happens ('rains in Curitiba'): affects pickups and deliveries there.")
    lane_only: bool = Field(description="True only when the text describes a specific route between an origin and a destination.")
    radius_center: str = Field(description="Center city for 'within N km of X', else empty string.")
    radius_km: float = Field(description="Radius in km, 0 when none.")
    radius_role: Literal["origin", "destination", "both", "none"]
    nationwide: bool
    legs: list[Leg] = Field(description="Explicit legs if stated (first_mile, linehaul, last_mile); empty to derive from places.")
    category_terms: list[str] = Field(description="Product types mentioned (e.g. 'furniture', 'perishable', 'bulky'); empty if none.")
    seller_ids: list[str] = Field(description="32-character seller ids mentioned, else empty.")
    start_offset_days: float = Field(description="Days from now until the disruption starts (0 = already started).")
    duration_days: float = Field(description="Duration in days (hours/24, weeks*7); 0 if not stated.")
    explicit_delay_days: float = Field(description="Explicit delay such as 'delayed by 3 days', else 0.")
    all_modes: bool = Field(description="True if the text says all transport modes (including air) are blocked.")
    confidence: float = Field(description="0..1 confidence in this interpretation.")
    notes: str = Field(description="One short sentence on assumptions made; empty if none.")


def _qualifier(text_norm: str, name_norm: str) -> Literal["state", "city"] | None:
    n = re.escape(name_norm)
    if re.search(rf"\b(state of|estado de|estado do|estado da) {n}\b", text_norm) or re.search(rf"\b{n} state\b", text_norm):
        return "state"
    if re.search(
        rf"\b(city of|cidade de|cidade do|cidade da|greater|metropolitan|metro|grande) {n}\b", text_norm
    ) or re.search(rf"\b{n} (city|metro|metropolitan area|capital)\b", text_norm):
        return "city"
    return None


class _GeoAcc:
    def __init__(self) -> None:
        self.states: list[str] = []
        self.cities: list[CityRef] = []
        self.radius: RadiusRef | None = None

    def add_state(self, uf: str) -> None:
        if uf not in self.states:
            self.states.append(uf)

    def add_city(self, ref: CityRef) -> None:
        if all((c.name, c.state) != (ref.name, ref.state) for c in self.cities):
            self.cities.append(ref)

    def build(self) -> GeoScope:
        return GeoScope(states=self.states, cities=self.cities, radius=self.radius)


def apply_ambiguities(spec: DisruptionSpec) -> DisruptionSpec:
    """Re-apply every ambiguity's chosen option to the geo scopes (idempotent)."""
    if not spec.ambiguities:
        return spec
    data = spec.model_copy(deep=True)
    for amb in data.ambiguities:
        for role in amb.roles:
            geo: GeoScope = data.origin if role == "origin" else data.destination
            option_states = {o.state for o in amb.options if o.kind == "state"}
            option_cities = {(o.city.name, o.city.state) for o in amb.options if o.kind == "city" and o.city}
            states = [s for s in geo.states if s not in option_states]
            cities = [c for c in geo.cities if (c.name, c.state) not in option_cities]
            chosen = amb.options[amb.chosen]
            if chosen.kind == "state":
                states.append(chosen.state)
            elif chosen.city is not None:
                cities.append(chosen.city)
            geo.states = states
            geo.cities = cities
    return DisruptionSpec.model_validate(data.model_dump())


def build_spec(
    raw: RawIntake,
    raw_text: str,
    gazetteer: Gazetteer,
    as_of: datetime,
    params: AssumptionParams,
    source: Literal["llm", "rules"],
) -> DisruptionSpec:
    notes: list[str] = [raw.notes.strip()] if raw.notes and raw.notes.strip() else []
    text_norm = normalize(raw_text)
    origin, dest = _GeoAcc(), _GeoAcc()
    ambiguities: dict[str, Ambiguity] = {}
    unresolved = 0

    def geos(roles: list[Role]) -> list[_GeoAcc]:
        return [origin if r == "origin" else dest for r in roles]

    def add_candidate(c: PlaceCandidate, roles: list[Role]) -> None:
        for g in geos(roles):
            if c.kind in ("state", "region"):
                for s in c.states:
                    g.add_state(s)
            elif c.city is not None:
                g.add_city(c.city.ref())

    def add_place(place: str, roles: list[Role]) -> None:
        nonlocal unresolved
        candidates = gazetteer.resolve(place)
        if not candidates:
            unresolved += 1
            notes.append(f"Could not resolve the place '{place}'; it was ignored.")
            return
        first = candidates[0]
        # Detect a state/city dual name, whether the place was given as a name or as a UF code.
        dual_name = None
        if first.kind == "state":
            name_norm = normalize(STATE_NAMES[first.value])
            if gazetteer.is_dual_name(name_norm) and name_norm in text_norm:
                dual_name = name_norm
        elif first.kind == "city" and first.city is not None and gazetteer.is_dual_name(first.city.name):
            dual_name = first.city.name
        if dual_name is None:
            add_candidate(first, roles)
            return
        uf = next(uf for uf, n in STATE_NAMES.items() if normalize(n) == dual_name)
        city_entry = gazetteer.city(dual_name, uf)
        qual = _qualifier(text_norm, dual_name)
        state_cand = PlaceCandidate("state", uf, f"{STATE_NAMES[uf]} (state)", [uf])
        if qual == "state" or city_entry is None:
            add_candidate(state_cand, roles)
            return
        city_cand = PlaceCandidate("city", city_entry.name, city_entry.label, [uf], city_entry)
        if qual == "city":
            add_candidate(city_cand, roles)
            return
        amb = ambiguities.get(dual_name)
        if amb is None:
            amb = Ambiguity(
                mention=STATE_NAMES[uf],
                roles=list(roles),
                options=[
                    AmbiguityOption(label=f"{STATE_NAMES[uf]} state ({uf})", kind="state", state=uf),
                    AmbiguityOption(
                        label=f"{city_entry.label} (city only)", kind="city", state=uf, city=city_entry.ref()
                    ),
                ],
                chosen=0,
                reason=(
                    f"'{STATE_NAMES[uf]}' is both a state and a city. Defaulting to the state for freight disruptions; "
                    "switch to the city if the event is local."
                ),
            )
            ambiguities[dual_name] = amb
        else:
            for r in roles:
                if r not in amb.roles:
                    amb.roles.append(r)
        add_candidate(state_cand, roles)

    explicit_legs = {leg for leg in raw.legs if leg in LEGS}
    # An event location ("strike in X") affects pickups and deliveries there, unless explicit legs narrow it:
    # a last-mile-only event touches deliveries into X, not sellers shipping from X.
    both_roles: list[Role] = ["origin", "destination"]
    if explicit_legs and not raw.nationwide:
        both_roles = [
            role for role, legs in (("origin", {"first_mile", "linehaul"}), ("destination", {"last_mile", "linehaul"}))
            if explicit_legs & legs
        ] or both_roles  # type: ignore[assignment]

    for p in raw.origin_places:
        add_place(p, ["origin"])
    for p in raw.destination_places:
        add_place(p, ["destination"])
    for p in raw.both_places:
        add_place(p, both_roles)

    if raw.radius_km > 0 and raw.radius_center.strip() and raw.radius_role != "none":
        cands = gazetteer.resolve(raw.radius_center)
        center = next((c for c in cands if c.kind == "city" and c.city is not None), None)
        if center is None and cands and cands[0].kind == "state":
            name_norm = normalize(STATE_NAMES[cands[0].value])
            entry = gazetteer.city(name_norm, cands[0].value)
            if entry is not None:
                center = PlaceCandidate("city", entry.name, entry.label, [entry.state], entry)
        if center is None or center.city is None or center.city.lat is None or center.city.lng is None:
            unresolved += 1
            notes.append(f"Could not locate the radius center '{raw.radius_center}'.")
        else:
            radius = RadiusRef(
                center=center.city.ref(), km=min(raw.radius_km, 1500), lat=center.city.lat, lng=center.city.lng
            )
            roles: list[Role] = ["origin", "destination"] if raw.radius_role == "both" else [raw.radius_role]  # type: ignore[list-item]
            for g in geos(roles):
                g.radius = radius
                g.cities = [c for c in g.cities if (c.name, c.state) != (radius.center.name, radius.center.state)]

    origin_geo, dest_geo = origin.build(), dest.build()

    legs: list[Leg] = [leg for leg in raw.legs if leg in LEGS]
    if raw.nationwide and not legs:
        legs = list(LEGS)
    if not legs:
        if not origin_geo.is_empty():
            legs += ["first_mile", "linehaul"]
        if not dest_geo.is_empty():
            legs += ["last_mile"] if origin_geo.is_empty() else ["linehaul", "last_mile"]
        if not legs:
            legs = ["first_mile"]  # seller/product-only scope (supplier or seller outage)
    if not origin_geo.is_empty() and not ({"first_mile", "linehaul"} & set(legs)):
        legs.append("first_mile")
        notes.append("Added the first-mile leg so origin places can match.")
    if not dest_geo.is_empty() and not ({"last_mile", "linehaul"} & set(legs)):
        legs.append("last_mile")
        notes.append("Added the last-mile leg so destination places can match.")

    match_mode: Literal["any", "lane"] = "any"
    if raw.lane_only and raw.origin_places and raw.destination_places and not raw.both_places:
        match_mode = "lane"

    duration = raw.duration_days
    explicit = raw.explicit_delay_days if raw.explicit_delay_days and raw.explicit_delay_days > 0 else None
    confidence = max(0.05, min(1.0, raw.confidence))
    if duration <= 0:
        if explicit:
            duration = explicit
        else:
            duration = 3.0
            confidence = max(0.05, confidence - 0.2)
            notes.append("No duration was stated; assumed 3 days.")
    duration = min(duration, 60.0)
    recovery = 0.0 if explicit is not None else float(math.ceil(params.recovery_factor * duration))
    confidence = max(0.05, confidence - 0.15 * unresolved)

    sellers = [s.lower() for s in raw.seller_ids if SELLER_ID_RE.match(s.lower())]
    title = raw.title.strip()[:200] or raw_text.strip()[:80]
    if len(title) < 3:
        title = "Disruption scenario"

    spec = DisruptionSpec(
        title=title,
        disruption_type=raw.disruption_type,
        legs=legs,
        origin=origin_geo,
        destination=dest_geo,
        match_mode=match_mode,
        nationwide=raw.nationwide,
        category_terms=raw.category_terms,
        seller_ids=sellers,
        start_at=as_of + timedelta(days=max(0.0, min(raw.start_offset_days, 30.0))),
        duration_days=duration,
        explicit_delay_days=explicit,
        recovery_days=recovery,
        all_modes=raw.all_modes,
        confidence=round(confidence, 2),
        ambiguities=list(ambiguities.values()),
        notes=notes,
        source=source,
    )
    return apply_ambiguities(spec)
