"""Resolve free-text product terms ("furniture", "bulky", "perishable") into category/class/profile filters.

Three signals, all recorded for explainability:
- lexical: term tokens match category name tokens (English or Portuguese);
- keyword: curated concept keywords (config/category_classes.yaml);
- semantic: cosine similarity between the term embedding and category document embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.core.policy_config import CategoryClassesConfig
from app.domain.models import ConceptMatch, ConceptResolution
from app.services.geo import normalize
from app.services.scope import ConceptFilter

# Thresholds chosen from `python -m app.cli eval` diagnostics (multilingual MiniLM, 20 labelled concept queries).
SEMANTIC_MIN_SIM = 0.62  # semantic-only match
LEXICAL_PARTIAL_MIN_SIM = 0.45  # lexical match covering only part of the term
SEMANTIC_MAX_HITS = 4
STOP = {"and", "the", "goods", "items", "products", "product", "item", "stuff", "all", "with", "for", "e", "de",
        "do", "da", "os", "as", "com", "para", "other", "others", "outros"}

PRODUCT_NOUNS = {
    "furniture", "electronic", "electronics", "toy", "toys", "book", "books", "appliance", "appliances", "computer",
    "computers", "phone", "phones", "perfume", "perfumes", "cosmetic", "cosmetics", "beauty", "sport", "sports",
    "garden", "tool", "tools", "kitchen", "fashion", "shoe", "shoes", "clothing", "clothes", "watch", "watches",
    "gift", "gifts", "baby", "pet", "food", "drink", "drinks", "flower", "flowers", "christmas", "music", "game",
    "games", "console", "consoles", "automotive", "stationery", "luggage", "mattress", "mattresses", "lighting",
    "housewares", "bedding", "moveis", "brinquedo", "brinquedos", "livro", "livros", "eletronicos",
    "eletrodomestico", "eletrodomesticos", "perfumaria", "esporte", "ferramenta", "ferramentas", "informatica",
    "telefonia", "relogio", "relogios", "bebe", "bebes", "alimento", "alimentos", "bebida", "bebidas", "flores",
    "perishable", "perishables", "fragile", "bulky", "oversized", "volumoso", "volumosos",
}
# Keyword-index entries that are too ambiguous in free disruption text (handled contextually or not at all).
RULE_VOCAB_EXCLUDE = {
    "heavy", "large", "big", "pesado", "pesados", "grande", "grandes", "home", "casa", "natal", "party",
    "fresh", "medical", "volume",
}

CLASS_LABELS = {
    "PERISHABLE": "Perishable goods", "ESSENTIAL": "Essential items", "FRAGILE": "Fragile items",
    "HIGH_VALUE_TECH": "High-value tech", "GIFT_SEASONAL": "Gifts & seasonal", "FURNITURE_HOME": "Furniture & home",
    "STANDARD": "Standard",
}


def _stem(tok: str) -> str:
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 4 and tok.endswith("s"):
        return tok[:-1]
    return tok


def _tokens(text: str) -> set[str]:
    return {_stem(t) for t in normalize(text.replace("_", " ")).split() if len(t) >= 3 and t not in STOP}


@dataclass
class CategoryInfo:
    name_pt: str
    name_en: str
    product_class: str


class ConceptResolver:
    def __init__(
        self,
        categories: list[CategoryInfo],
        classes_cfg: CategoryClassesConfig,
        heavy_min_kg: float,
        category_vectors: np.ndarray | None = None,
        embed_fn=None,
    ):
        self.categories = categories
        self.cfg = classes_cfg
        self.heavy_min_kg = heavy_min_kg
        self.cat_tokens = [(_tokens(c.name_en) | _tokens(c.name_pt)) for c in categories]
        self.vectors = None
        if category_vectors is not None and len(category_vectors) == len(categories):
            norms = np.linalg.norm(category_vectors, axis=1, keepdims=True)
            self.vectors = category_vectors / np.clip(norms, 1e-9, None)
        self.embed_fn = embed_fn  # callable(list[str]) -> np.ndarray
        self.keyword_index: dict[str, tuple[str, str]] = {}
        for profile, kws in classes_cfg.concept_keywords.profiles.items():
            for kw in kws:
                self.keyword_index[_stem(normalize(kw))] = ("profile", profile)
        for cls, kws in classes_cfg.concept_keywords.classes.items():
            for kw in kws:
                self.keyword_index.setdefault(_stem(normalize(kw)), ("class", cls))

    def vocabulary(self) -> set[str]:
        """Words the offline rule parser treats as product terms.

        Curated on purpose: category-name tokens include generic words ("general", "room", "table", "services")
        and city names ("natal") that would turn ordinary disruption text into a product filter. Size adjectives
        ("heavy", "large") are handled by the parser only when followed by a goods noun.
        """
        keywords = {k for k in self.keyword_index if k not in RULE_VOCAB_EXCLUDE}
        return keywords | PRODUCT_NOUNS

    def resolve(self, terms: list[str]) -> tuple[list[ConceptResolution], ConceptFilter]:
        flt = ConceptFilter(heavy_min_kg=self.heavy_min_kg)
        resolutions: list[ConceptResolution] = []
        sem_vectors = None
        if terms and self.vectors is not None and self.embed_fn is not None:
            vecs = np.asarray(self.embed_fn(terms), dtype=np.float32)
            sem_vectors = vecs / np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9, None)

        for i, term in enumerate(terms):
            res = ConceptResolution(term=term)
            toks = _tokens(term)
            seen: set[tuple[str, str]] = set()

            def add(kind: str, value: str, label: str, score: float, via: str) -> None:
                if (kind, value) in seen:
                    return
                seen.add((kind, value))
                res.matches.append(ConceptMatch(kind=kind, value=value, label=label, score=round(score, 3), via=via))  # type: ignore[arg-type]
                if kind == "category":
                    flt.categories[value] = label
                elif kind == "class":
                    flt.classes.add(value)
                elif kind == "profile":
                    flt.heavy_bulky = True

            for tok in toks:
                hit = self.keyword_index.get(tok)
                if hit:
                    kind, value = hit
                    label = "Heavy / bulky items" if kind == "profile" else CLASS_LABELS.get(value, value)
                    add(kind, value, label, 1.0, "keyword")
            sims = (self.vectors @ sem_vectors[i]) if (sem_vectors is not None and self.vectors is not None) else None
            for j, (cat, ctoks) in enumerate(zip(self.categories, self.cat_tokens, strict=True)):
                shared = toks & ctoks
                if not shared:
                    continue
                coverage = len(shared) / len(toks)
                # A category matching only part of a multi-word term ("car accessories" vs "bags_accessories")
                # must also be semantically close; full-coverage matches are always kept.
                if coverage >= 1.0:
                    add("category", cat.name_pt, cat.name_en, 1.0, "lexical")
                elif sims is not None and sims[j] >= LEXICAL_PARTIAL_MIN_SIM:
                    add("category", cat.name_pt, cat.name_en, round(float(sims[j]), 3), "lexical")
                elif sims is None and coverage >= 0.5:
                    add("category", cat.name_pt, cat.name_en, coverage, "lexical")
            if sims is not None:
                order = np.argsort(-sims)[:SEMANTIC_MAX_HITS]
                for j in order:
                    if sims[j] >= SEMANTIC_MIN_SIM:
                        cat = self.categories[int(j)]
                        add("category", cat.name_pt, cat.name_en, float(sims[j]), "semantic")
            resolutions.append(res)
        return resolutions, flt
