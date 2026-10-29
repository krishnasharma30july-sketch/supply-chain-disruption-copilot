"""Extract numbers, money amounts, percentages and dates from generated text and check them against facts."""

from __future__ import annotations

import re

SAFE_TOKENS_RE = re.compile(r"\b24\s?/\s?7\b|\b24\s?h(?:ours)?\b", re.I)  # "24/7 support" is not a date
ORDINAL_RE = re.compile(r"\b\d{1,2}(?:st|nd|rd|th|º|ª)\b", re.I)
RELATIVE_DATE_RE = re.compile(
    r"\b(tomorrow|tonight|next week|this week|next month|this weekend|next weekend|end of (?:the )?week|"
    r"(?:next |this )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"amanh[ãa]|pr[óo]xima semana|esta semana|fim de semana|"
    r"(?:segunda|ter[çc]a|quarta|quinta|sexta)-feira|s[áa]bado|domingo)\b",
    re.I,
)
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30,
    "a couple of": 2, "a few": 3, "several": 3,
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "três": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
    "oito": 8, "nove": 9, "dez": 10, "quinze": 15, "vinte": 20, "trinta": 30, "alguns": 3, "algumas": 3,
}
_UNITS = r"(?:business |working |calendar |úteis |uteis )?(?:days?|weeks?|hours?|months?|dias?|semanas?|horas?|m[eê]s(?:es)?)"
NUMBER_WORD_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(w) for w in NUMBER_WORDS), key=len, reverse=True)) + r")\s+" + _UNITS + r"\b",
    re.I,
)
MONEY_RE = re.compile(r"R\$\s?(\d[\d.,]*\d|\d)")
PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s?%")
NUMBER_RE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*(?![\w])")
DATE_PATTERNS = [
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?"),
    re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}\s+de\s+(?:janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\b", re.I),
]


def parse_amount(raw: str) -> float | None:
    """Parse '1,234.56', '1.234,56', '42.10', '42,10', '1500' deterministically."""
    s = raw.strip().rstrip(".,")
    if not s:
        return None
    try:
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                return float(s.replace(".", "").replace(",", "."))
            return float(s.replace(",", ""))
        if "," in s:
            if re.search(r",\d{1,2}$", s):
                return float(s.replace(",", "."))
            return float(s.replace(",", ""))
        if "." in s:
            if re.search(r"\.\d{1,2}$", s):
                return float(s)
            return float(s.replace(".", ""))
        return float(s)
    except ValueError:
        return None


def _close(value: float, allowed: list[float], tol: float = 0.006) -> bool:
    return any(abs(value - a) <= tol for a in allowed)


def scan(
    text: str,
    *,
    allowed_dates: list[str],
    allowed_money: list[float],
    allowed_percents: list[float],
    allowed_numbers: list[float],
    strip_tokens: list[str] | None = None,
) -> list[str]:
    """Return a list of human-readable violations (empty = every figure is grounded)."""
    problems: list[str] = []
    work = text
    for token in strip_tokens or []:
        if token:
            work = work.replace(token, " ")
    work = SAFE_TOKENS_RE.sub(" ", work)
    for d in sorted(allowed_dates, key=len, reverse=True):
        if d:
            work = work.replace(d, " ")
    for pat in DATE_PATTERNS:
        for m in pat.finditer(work):
            problems.append(f"Date not in the facts: '{m.group(0)}'")
        work = pat.sub(" ", work)
    # Dates the facts never use (they are always full dates): ordinals ("by the 15th") and relative dates.
    for m in ORDINAL_RE.finditer(work):
        problems.append(f"Ordinal date not in the facts: '{m.group(0)}'")
    work = ORDINAL_RE.sub(" ", work)
    for m in RELATIVE_DATE_RE.finditer(work):
        problems.append(f"Relative date not allowed (use the exact dates given): '{m.group(0)}'")
    work = RELATIVE_DATE_RE.sub(" ", work)

    def number_word_sub(m: re.Match) -> str:
        value = float(NUMBER_WORDS[m.group(1).lower()])
        if not _close(value, allowed_numbers, 0.01):
            problems.append(f"Duration not in the facts: '{m.group(0)}'")
        return " "

    work = NUMBER_WORD_RE.sub(number_word_sub, work)

    def money_sub(m: re.Match) -> str:
        val = parse_amount(m.group(1))
        if val is None or not _close(val, allowed_money):
            problems.append(f"Amount not in the facts: 'R$ {m.group(1)}'")
        return " "

    work = MONEY_RE.sub(money_sub, work)

    def pct_sub(m: re.Match) -> str:
        val = parse_amount(m.group(1))
        if val is None or not _close(val, allowed_percents, 0.01):
            problems.append(f"Percentage not in the facts: '{m.group(0)}'")
        return " "

    work = PERCENT_RE.sub(pct_sub, work)
    for m in NUMBER_RE.finditer(work):
        val = parse_amount(m.group(0))
        if val is None or not _close(val, allowed_numbers, 0.01):
            problems.append(f"Number not in the facts: '{m.group(0)}'")
    return problems
