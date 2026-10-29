"""Geography: name normalization, Brazilian states/regions, a city gazetteer and distances."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from unidecode import unidecode

from app.domain.models import UF_CODES, CityRef

STATE_NAMES: dict[str, str] = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapa", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceara",
    "DF": "Distrito Federal", "ES": "Espirito Santo", "GO": "Goias", "MA": "Maranhao", "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul", "MG": "Minas Gerais", "PA": "Para", "PB": "Paraiba", "PR": "Parana",
    "PE": "Pernambuco", "PI": "Piaui", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondonia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "Sao Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}

REGIONS: dict[str, list[str]] = {
    "north": ["AC", "AP", "AM", "PA", "RO", "RR", "TO"],
    "northeast": ["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"],
    "center-west": ["DF", "GO", "MT", "MS"],
    "southeast": ["ES", "MG", "RJ", "SP"],
    "south": ["PR", "RS", "SC"],
}
REGION_ALIASES: dict[str, str] = {
    "north": "north", "norte": "north", "north region": "north",
    "northeast": "northeast", "nordeste": "northeast", "north east": "northeast", "north-east": "northeast",
    "center west": "center-west", "centre west": "center-west", "center-west": "center-west",
    "centro oeste": "center-west", "midwest": "center-west", "mid west": "center-west",
    "southeast": "southeast", "sudeste": "southeast", "south east": "southeast", "south-east": "southeast",
    "south": "south", "south region": "south", "southern brazil": "south",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str | None) -> str:
    """Lowercase, strip accents, collapse non-alphanumerics to single spaces."""
    if not text:
        return ""
    return _NON_ALNUM.sub(" ", unidecode(str(text)).lower()).strip()


_UF_SUFFIX = re.compile(r"^(.*?)\s*[-/,(]\s*([a-z]{2})\)?\s*$")


def normalize_city(raw: str | None) -> str:
    """Normalize messy Olist city spellings ('sao paulo - sp', 'São Paulo/SP') to 'sao paulo'."""
    if not raw:
        return ""
    text = unidecode(str(raw)).lower().strip()
    m = _UF_SUFFIX.match(text)
    if m and m.group(2).upper() in UF_CODES:
        text = m.group(1)
    return normalize(text)


STATE_BY_NAME: dict[str, str] = {normalize(name): uf for uf, name in STATE_NAMES.items()}


def title_city(city_norm: str) -> str:
    small = {"de", "da", "do", "das", "dos", "e"}
    words = city_norm.split()
    return " ".join(w if (i > 0 and w in small) else w.capitalize() for i, w in enumerate(words))


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


@dataclass(slots=True)
class CityEntry:
    name: str
    state: str
    label: str
    lat: float | None
    lng: float | None
    weight: int

    def ref(self) -> CityRef:
        return CityRef(name=self.name, state=self.state, label=self.label)


@dataclass(slots=True)
class PlaceCandidate:
    kind: str  # state | city | region
    value: str  # UF code, region key, or city name
    label: str
    states: list[str]
    city: CityEntry | None = None


class Gazetteer:
    """Resolves place names to states, regions and cities using the cities seen in the dataset."""

    def __init__(self, cities: list[CityEntry], min_mention_weight: int = 20):
        self.cities_by_name: dict[str, list[CityEntry]] = {}
        for c in cities:
            self.cities_by_name.setdefault(c.name, []).append(c)
        for entries in self.cities_by_name.values():
            entries.sort(key=lambda e: e.weight, reverse=True)
        self.min_mention_weight = min_mention_weight
        self.max_city_words = max((len(n.split()) for n in self.cities_by_name), default=1)

    # ----------------------------------------------------------------- lookups

    def city(self, name: str, state: str | None = None) -> CityEntry | None:
        entries = self.cities_by_name.get(normalize_city(name))
        if not entries:
            return None
        if state:
            for e in entries:
                if e.state == state.upper():
                    return e
            return None
        return entries[0]

    def is_dual_name(self, norm_name: str) -> bool:
        """True when a name is both a state and a city (e.g. 'sao paulo', 'rio de janeiro')."""
        uf = STATE_BY_NAME.get(norm_name)
        return bool(uf and any(e.state == uf for e in self.cities_by_name.get(norm_name, [])))

    def resolve(self, raw: str) -> list[PlaceCandidate]:
        """Resolve a free-text place to candidates (most likely first). Empty list when unknown."""
        text = raw.strip()
        if not text:
            return []
        # "City, UF" / "City - UF" / "City/UF"
        m = re.match(r"^(.+?)\s*[,/\-]\s*([A-Za-z]{2})$", text)
        if m and m.group(2).upper() in UF_CODES:
            entry = self.city(m.group(1), m.group(2).upper())
            if entry:
                return [PlaceCandidate("city", entry.name, entry.label, [entry.state], entry)]
        if text.upper() in UF_CODES and len(text) == 2:
            uf = text.upper()
            return [PlaceCandidate("state", uf, f"{STATE_NAMES[uf]} (state)", [uf])]
        norm = normalize(text)
        for prefix in ("state of ", "estado de ", "estado do ", "estado da "):
            if norm.startswith(prefix):
                uf = STATE_BY_NAME.get(norm[len(prefix):])
                if uf:
                    return [PlaceCandidate("state", uf, f"{STATE_NAMES[uf]} (state)", [uf])]
        for prefix in ("city of ", "cidade de ", "cidade do ", "cidade da "):
            if norm.startswith(prefix):
                entry = self.city(norm[len(prefix):])
                if entry:
                    return [PlaceCandidate("city", entry.name, entry.label, [entry.state], entry)]
        for suffix in (" state", " estado"):
            if norm.endswith(suffix):
                uf = STATE_BY_NAME.get(norm[: -len(suffix)])
                if uf:
                    return [PlaceCandidate("state", uf, f"{STATE_NAMES[uf]} (state)", [uf])]
        for suffix in (" city", " cidade"):
            if norm.endswith(suffix):
                entry = self.city(norm[: -len(suffix)])
                if entry:
                    return [PlaceCandidate("city", entry.name, entry.label, [entry.state], entry)]
        region = REGION_ALIASES.get(norm) or REGION_ALIASES.get(
            re.sub(r"^(?:the |regiao |a regiao )|(?: region| regiao)$", "", norm).strip()
        )
        if region:
            return [PlaceCandidate("region", region, f"{region.title()} region", list(REGIONS[region]))]
        out: list[PlaceCandidate] = []
        uf = STATE_BY_NAME.get(norm)
        if uf:
            out.append(PlaceCandidate("state", uf, f"{STATE_NAMES[uf]} (state)", [uf]))
        entry = self.city(norm)
        if entry:
            # A dual name (e.g. Sao Paulo) yields both candidates; the state stays first (assumption A18).
            if not uf or entry.state == uf:
                out.append(PlaceCandidate("city", entry.name, entry.label, [entry.state], entry))
        return out

    # ----------------------------------------------------------------- free-text scanning

    def find_mentions(self, text: str) -> list[tuple[int, int, str, list[PlaceCandidate]]]:
        """Scan raw text for place mentions. Returns (start_token, end_token, surface, candidates).

        Cities must appear capitalized in the text and be reasonably frequent in the dataset to avoid
        matching ordinary words; UF codes must be uppercase.
        """
        tokens = [(m.group(0), m.start()) for m in re.finditer(r"[A-Za-zÀ-ÿ']+", text)]
        lenient = text == text.lower()  # an all-lowercase query gives no capitalization signal
        mentions: list[tuple[int, int, str, list[PlaceCandidate]]] = []
        i = 0
        max_n = max(4, self.max_city_words)
        while i < len(tokens):
            found = None
            for n in range(min(max_n, len(tokens) - i), 0, -1):
                words = [t[0] for t in tokens[i : i + n]]
                surface = " ".join(words)
                norm = normalize(surface)
                if not norm:
                    continue
                if n == 1 and len(words[0]) == 2:
                    if words[0].isupper() and words[0] in UF_CODES and not text.isupper():
                        uf = words[0]
                        found = (n, surface, [PlaceCandidate("state", uf, f"{STATE_NAMES[uf]} (state)", [uf])])
                        break
                    continue
                region = REGION_ALIASES.get(norm)
                if region and n <= 3:
                    found = (n, surface, [PlaceCandidate("region", region, f"{region.title()} region", list(REGIONS[region]))])
                    break
                candidates: list[PlaceCandidate] = []
                capitalized = words[0][:1].isupper() or lenient
                # State and city names must be capitalized: "para" (= "for" in Portuguese) is also the state Para.
                uf = STATE_BY_NAME.get(norm) if capitalized else None
                if uf:
                    candidates.append(PlaceCandidate("state", uf, f"{STATE_NAMES[uf]} (state)", [uf]))
                entries = self.cities_by_name.get(norm)
                if entries and capitalized:
                    best = entries[0]
                    if best.weight >= self.min_mention_weight and (not uf or best.state == uf):
                        candidates.append(PlaceCandidate("city", best.name, best.label, [best.state], best))
                    elif uf:
                        same_state = [e for e in entries if e.state == uf]
                        if same_state:
                            candidates.append(PlaceCandidate("city", same_state[0].name, same_state[0].label, [uf], same_state[0]))
                if candidates:
                    found = (n, surface, candidates)
                    break
            if found:
                n, surface, candidates = found
                mentions.append((i, i + n, surface, candidates))
                i += n
            else:
                i += 1
        return mentions


def expand_states(candidates: list[PlaceCandidate]) -> list[str]:
    out: list[str] = []
    for c in candidates:
        for s in c.states:
            if s not in out:
                out.append(s)
    return out
