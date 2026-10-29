"""Locale-aware formatting. The same functions format LLM facts, templates and the QA allow-lists."""

from __future__ import annotations

from datetime import datetime

MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_date(dt: datetime, locale: str) -> str:
    if locale == "pt-BR":
        return f"{dt.day:02d}/{dt.month:02d}/{dt.year}"
    return f"{MONTHS_EN[dt.month - 1]} {dt.day}, {dt.year}"


def fmt_money(value: float, locale: str) -> str:
    s = f"{value:,.2f}"  # 1,234.56
    if locale == "pt-BR":
        s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {s}"


def fmt_pct(value: float) -> str:
    return f"{value:g}%"


# Olist category codes are unaccented ("moveis_decoracao"); restore the accents for customer-facing pt-BR text.
PT_ACCENTS = {
    "moveis": "móveis", "decoracao": "decoração", "eletronicos": "eletrônicos", "informatica": "informática",
    "acessorios": "acessórios", "saude": "saúde", "domesticas": "domésticas", "eletrodomesticos": "eletrodomésticos",
    "relogios": "relógios", "construcao": "construção", "iluminacao": "iluminação", "seguranca": "segurança",
    "eletroportateis": "eletroportáteis", "portateis": "portáteis", "cafe": "café", "climatizacao": "climatização",
    "bebes": "bebês", "audio": "áudio", "musica": "música", "tecnicos": "técnicos", "musicais": "musicais",
    "servico": "serviço", "servicos": "serviços", "colchao": "colchão", "comercio": "comércio",
    "industria": "indústria", "sinalizacao": "sinalização", "impressao": "impressão", "agro": "agro",
    "area": "área", "conforto": "conforto", "preparadores": "preparadores", "eletronico": "eletrônico",
}
PT_SMALL = {"e", "de", "da", "do", "das", "dos", "para"}


def pretty_category_pt(name_pt: str | None) -> str:
    if not name_pt:
        return "Item"
    words = [w for w in name_pt.split("_") if w and not w.isdigit()]
    out = []
    for i, w in enumerate(words):
        w = PT_ACCENTS.get(w, w)
        out.append(w if (i > 0 and w in PT_SMALL) else w[:1].upper() + w[1:])
    return " ".join(out) or "Item"


def pretty_category(name_en: str) -> str:
    """Customer-facing category label. Olist has duplicate categories with a numeric suffix
    ("home_appliances_2"); the suffix is dropped so customers never see "Home Appliances 2"."""
    if not name_en or name_en == "unknown":
        return "Item"
    words = [w for w in name_en.replace("_", " ").split() if not w.isdigit()]
    return " ".join(w.capitalize() for w in words) or "Item"
