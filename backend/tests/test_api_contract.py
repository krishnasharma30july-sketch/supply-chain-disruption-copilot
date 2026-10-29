"""Contract test: the hand-written TypeScript API types (web/src/api/types.ts) must match the Pydantic models.

Fails when a field is added, removed or renamed on one side only. Skipped when the web sources are not present
(e.g. inside the backend image); CI runs it from the repository checkout.
"""

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.api import schemas
from app.api.v1.auth import AuthConfig, TokenOut
from app.api.v1.users import AdminUserOut
from app.domain.models import DisruptionSpec, MitigationDecision, RunOptions
from app.services.analysis import PreviewCounts

TYPES_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "types.ts"

# TypeScript interface -> Pydantic model it mirrors.
PAIRS: dict[str, type[BaseModel]] = {
    "DisruptionSpec": DisruptionSpec,
    "PreviewCounts": PreviewCounts,
    "OptionPreview": schemas.OptionPreview,
    "ParseResponse": schemas.ParseResponse,
    "RunOptions": RunOptions,
    "Run": schemas.RunDetail,
    "RunEvent": schemas.EventOut,
    "OrderRow": schemas.OrderRow,
    "Communication": schemas.CommunicationOut,
    "Decision": MitigationDecision,
    "Summary": schemas.SummaryOut,
    "SearchHit": schemas.SearchHitOut,
    "SearchResponse": schemas.SearchResponse,
    "AuthConfig": AuthConfig,
    "AdminUser": AdminUserOut,
    "LoginResponse": TokenOut,
}
# Fields the UI deliberately does not use.
TS_MAY_OMIT = {"Decision": {"order_id", "allowed_actions", "agent_trace"}}

_IFACE = re.compile(r"export interface (\w+)(?: extends (\w+))? \{")
_FIELD = re.compile(r"\s*(\w+)(\?)?\s*:")


def _top_level_fields(src: str, start: int) -> tuple[str, int]:
    """Text of the interface body with nested braces blanked out, and the index after the closing brace."""
    depth, out, i = 1, [], start
    while depth:
        ch = src[i]
        depth += (ch == "{") - (ch == "}")
        out.append(ch if depth == 1 and ch not in "{}" else " ")
        i += 1
    return "".join(out), i


def _ts_interfaces() -> dict[str, dict[str, bool]]:
    """name -> {field: optional}; inherited fields are merged in."""
    src = TYPES_TS.read_text(encoding="utf-8")
    raw: dict[str, tuple[str | None, str]] = {}
    for m in _IFACE.finditer(src):
        body, _ = _top_level_fields(src, m.end())
        # Statements are separated by ";" or newlines at the top level.
        stmts = re.split(r"[;\n]", body)
        raw[m.group(1)] = (m.group(2), "\n".join(stmts))

    def fields(name: str) -> dict[str, bool]:
        parent, body = raw[name]
        out = fields(parent) if parent in raw else {}
        for stmt in body.split("\n"):
            f = _FIELD.match(stmt)
            if f and not stmt.strip().startswith(("//", "[")):
                out[f.group(1)] = bool(f.group(2))
        return out

    return {name: fields(name) for name in raw}


@pytest.mark.skipif(not TYPES_TS.exists(), reason="web sources not available")
@pytest.mark.parametrize("ts_name", sorted(PAIRS))
def test_typescript_types_match_pydantic(ts_name):
    ts = _ts_interfaces()
    assert ts_name in ts, f"interface {ts_name} not found in types.ts"
    py_fields = set(PAIRS[ts_name].model_fields)
    ts_fields = ts[ts_name]
    missing_in_ts = py_fields - set(ts_fields) - TS_MAY_OMIT.get(ts_name, set())
    unknown_in_ts = {f for f, optional in ts_fields.items() if f not in py_fields and not optional}
    assert not missing_in_ts, f"{ts_name}: fields returned by the API but missing in types.ts: {sorted(missing_in_ts)}"
    assert not unknown_in_ts, f"{ts_name}: required fields in types.ts that the API does not return: {sorted(unknown_in_ts)}"
