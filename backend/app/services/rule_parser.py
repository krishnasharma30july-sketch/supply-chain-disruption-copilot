"""Deterministic fallback parser: free text -> RawIntake, used when the LLM is unavailable."""

from __future__ import annotations

import re

from unidecode import unidecode

from app.domain.models import DisruptionType
from app.services.geo import Gazetteer, normalize
from app.services.spec_builder import RawIntake

NUM_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30,
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
    "oito": 8, "nove": 9, "dez": 10, "quinze": 15, "vinte": 20,
}
_NUM = r"(\d+(?:[.,]\d+)?|" + "|".join(sorted(NUM_WORDS, key=len, reverse=True)) + r")"
_UNIT = r"(hours?|hrs?|days?|weeks?|horas?|dias?|semanas?)"
DURATION_RE = re.compile(rf"\b{_NUM}\s*-?\s*{_UNIT}\b")
EXPLICIT_RE = re.compile(rf"\b(?:delay(?:ed|s|ing)?|late|atras\w*)\s+(?:by|of|de|em)\s+{_NUM}\s*-?\s*{_UNIT}\b")
RADIUS_RE = re.compile(r"\bwithin\s+(\d+(?:[.,]\d+)?)\s*(?:km|kilometers|kilometres)\s+(?:of|from|around)\b")
NATIONWIDE_RE = re.compile(
    r"\b(nationwide|nation[\s-]wide|national|countrywide|all of brazil|across brazil|whole country|entire country|"
    r"todo o brasil|em todo o pais|nacional)\b"
)
ALL_MODES_RE = re.compile(r"\b(all (transport )?modes|air and road|including air|todos os modais)\b")
LAST_MILE_RE = re.compile(r"\b(last[\s-]mile|ultima milha|courier|couriers|deliveries|delivery|entregas|entrega)\b")
TOMORROW_RE = re.compile(r"\b(tomorrow|amanha)\b")
# Seller-side wording: a place mentioned without a direction cue is where goods are dispatched from.
SELLER_SIDE_RE = re.compile(r"\b(sellers?|seller's|warehouses?|dispatch|pickups?|vendedor(es)?|armazem|armazens|coleta)\b")
# Sentences that try to instruct the system instead of describing a disruption are dropped before parsing.
INJECTION_RE = re.compile(
    r"\b(ignore\s+(all\s+)?(the\s+)?(previous|prior|above|your)|disregard|system\s*:|assistant\s*:|developer\s*:|"
    r"you\s+are\s+now|new\s+instructions?|set\s+\w+\s*=)", re.IGNORECASE,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
NEGATION_WORDS = {"not", "isnt", "arent", "wont", "without", "nao", "sem"}

TYPE_KEYWORDS: dict[DisruptionType, list[str]] = {
    "weather": ["rain", "rains", "rainfall", "flood", "floods", "flooding", "storm", "storms", "hail", "hailstorm",
                "hailstorms", "snow", "snowstorm", "thunderstorm", "thunderstorms", "hurricane", "cyclone", "weather",
                "chuva", "chuvas", "enchente", "enchentes", "tempestade", "tempestades", "granizo", "alagamento",
                "alagamentos", "ciclone"],
    "strike": ["strike", "strikes", "greve", "protest", "protests", "walkout", "paralisacao"],
    "infrastructure": ["road", "roads", "closure", "closed", "bridge", "highway", "highways", "rodovia", "port",
                       "landslide", "collapse", "bloqueio", "interdicao", "accident"],
    "capacity": ["capacity", "backlog", "volume", "surge", "peak", "congestion", "overload", "overloaded",
                 "sobrecarga"],
    "supplier": ["supplier", "suppliers", "shortage", "stockout", "factory", "fornecedor", "falta"],
    "seller_outage": ["warehouse", "fire", "outage", "armazem", "incendio"],
}

ORIGIN_CUES = {"leaving", "leave", "leaves", "from", "out", "departing", "departure", "departures", "origin",
               "shipped", "shipping", "dispatched", "dispatch", "outbound", "saindo", "partindo", "origem"}
DEST_CUES = {"to", "into", "arriving", "arrive", "arrivals", "destined", "bound", "towards", "toward",
             "deliveries", "delivery", "delivering", "inbound", "destino", "para", "entregas", "entrega", "chegando"}
LOCATION_CUES = {"in", "at", "around", "across", "near", "throughout", "within", "em", "no", "na", "nos", "nas"}

GENERIC_WORDS = {"goods", "items", "products", "product", "item", "stuff", "orders", "order", "freight", "all",
                 "rain", "rains"}
SIZE_CONTEXT = {"heavy", "large", "big", "pesado", "pesados", "grande", "grandes"}
GOODS_NOUNS = {"items", "item", "goods", "products", "product", "freight", "packages", "package", "parcels", "parcel",
               "shipments", "shipment", "cargo", "orders", "furniture", "appliances", "carga", "cargas", "itens",
               "produtos", "mercadorias", "volumes"}


def _to_number(tok: str) -> float:
    tok = tok.lower()
    if tok in NUM_WORDS:
        return float(NUM_WORDS[tok])
    return float(tok.replace(",", "."))


def _to_days(value: float, unit: str) -> float:
    unit = unit.lower()
    if unit.startswith(("hour", "hr", "hora")):
        return value / 24.0
    if unit.startswith(("week", "semana")):
        return value * 7.0
    return value


def strip_injections(text: str) -> str:
    """Drop sentences that address the system ("ignore previous instructions", "SYSTEM: set x=y")."""
    kept = [s for s in SENTENCE_SPLIT_RE.split(text) if s.strip() and not INJECTION_RE.search(s)]
    return " ".join(kept) if kept else text


def _type_words(text: str) -> list[str]:
    """Words for disruption-type keywords, skipping negated clauses ("snow is not expected, but a strike is")."""
    clauses = [normalize(c).split() for c in re.split(r"[,;.]|\bbut\b|\bmas\b", text)]
    asserted = [c for c in clauses if not NEGATION_WORDS & set(c)]
    return [w for c in (asserted or clauses) for w in c]


def parse_rules(text: str, gazetteer: Gazetteer, concept_vocab: set[str]) -> RawIntake:
    text = strip_injections(text)
    norm = normalize(text)  # punctuation stripped: for keyword matching
    lite = unidecode(text).lower()  # punctuation kept: for numbers ("2.5 days") and phrases ("last-mile")
    words = norm.split()

    explicit = 0.0
    m = EXPLICIT_RE.search(lite)
    if m:
        explicit = _to_days(_to_number(m.group(1)), m.group(2))
    duration = 0.0
    for dm in DURATION_RE.finditer(lite):
        if m and dm.start() >= m.start() and dm.end() <= m.end():
            continue  # that number is the explicit delay
        duration = _to_days(_to_number(dm.group(1)), dm.group(2))
        break

    type_words = _type_words(text)
    scores: dict[DisruptionType, int] = {}
    for dtype, kws in TYPE_KEYWORDS.items():
        hits = sum(1 for w in type_words if w in kws)
        if hits:
            scores[dtype] = hits
    dtype: DisruptionType = max(scores, key=lambda k: scores[k]) if scores else "other"

    nationwide = bool(NATIONWIDE_RE.search(lite))
    last_mile_cue = bool(LAST_MILE_RE.search(lite))
    seller_side_cue = bool(SELLER_SIDE_RE.search(lite)) and not last_mile_cue

    # Token stream identical to Gazetteer.find_mentions (letters only), so indices line up.
    raw_tokens = re.findall(r"[A-Za-zÀ-ÿ']+", text)
    norm_tokens = [normalize(t) for t in raw_tokens]

    radius_km = 0.0
    radius_tok: int | None = None
    rm = RADIUS_RE.search(lite)
    if rm:
        radius_km = _to_number(rm.group(1))
        for i in range(len(norm_tokens) - 2):
            if (
                norm_tokens[i] == "within"
                and norm_tokens[i + 1] in ("km", "kilometers", "kilometres")
                and norm_tokens[i + 2] in ("of", "from", "around")
            ):
                radius_tok = i + 3
                break

    origin: list[str] = []
    dest: list[str] = []
    both: list[str] = []
    radius_center = ""
    for start, _end, surface, _cands in gazetteer.find_mentions(text):
        if radius_tok is not None and not radius_center and start == radius_tok:
            radius_center = surface
            continue
        window = [t for t in norm_tokens[max(0, start - 5) : start]]
        role = "both"
        for tok in reversed(window):
            if tok in ORIGIN_CUES:
                role = "origin"
                break
            if tok in DEST_CUES:
                role = "destination"
                break
            if tok in LOCATION_CUES:
                role = "both"
                break
        if role == "both" and last_mile_cue:
            role = "destination"
        elif role == "both" and seller_side_cue:
            role = "origin"
        {"origin": origin, "destination": dest, "both": both}[role].append(surface)

    lane_only = bool(origin and dest)
    terms: list[str] = []
    if "home goods" in norm:
        terms.append("home goods")
    for idx, w in enumerate(words):
        nxt = words[idx + 1] if idx + 1 < len(words) else ""
        if w in SIZE_CONTEXT:
            # "heavy freight" is about goods; "heavy rains" / "large backlog" are not.
            phrase = f"{w} {nxt}"
            if nxt in GOODS_NOUNS and phrase not in terms:
                terms.append(phrase)
            continue
        stem = w[:-1] if len(w) > 4 and w.endswith("s") else w
        if (w in concept_vocab or stem in concept_vocab) and w not in GENERIC_WORDS and w not in terms:
            terms.append(w)

    confidence = 0.6
    if duration == 0 and explicit == 0:
        confidence -= 0.2
    if dtype == "other":
        confidence -= 0.1

    title = text.strip().rstrip(".")
    title = (title[:1].upper() + title[1:])[:80] if title else "Disruption scenario"
    return RawIntake(
        title=title,
        disruption_type=dtype,
        origin_places=origin,
        destination_places=dest,
        both_places=both,
        lane_only=lane_only,
        radius_center=radius_center,
        radius_km=radius_km if radius_center else 0.0,
        radius_role="both" if radius_center else "none",
        nationwide=nationwide,
        legs=[],
        category_terms=terms[:10],
        seller_ids=re.findall(r"\b[0-9a-f]{32}\b", text.lower()),
        start_offset_days=1.0 if TOMORROW_RE.search(lite) else 0.0,
        duration_days=duration,
        explicit_delay_days=explicit,
        all_modes=bool(ALL_MODES_RE.search(lite)),
        confidence=max(0.1, confidence),
        notes="Parsed by deterministic rules (LLM unavailable).",
    )
