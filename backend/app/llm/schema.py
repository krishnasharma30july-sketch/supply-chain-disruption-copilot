"""Convert Pydantic models to the OpenAPI-subset schema accepted by Gemini's response_schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

_TYPES = {"string": "STRING", "number": "NUMBER", "integer": "INTEGER", "boolean": "BOOLEAN"}


def to_gemini_schema(model: type[BaseModel]) -> dict[str, Any]:
    root = model.model_json_schema()
    defs: dict[str, Any] = root.get("$defs", {})

    def conv(node: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in node:
            return conv(defs[node["$ref"].split("/")[-1]])
        if "allOf" in node and len(node["allOf"]) == 1:
            return conv(node["allOf"][0])
        if "anyOf" in node:
            options = [n for n in node["anyOf"] if n.get("type") != "null"]
            out = conv(options[0])
            if len(options) < len(node["anyOf"]):
                out["nullable"] = True
            return out
        out: dict[str, Any]
        if "enum" in node:
            out = {"type": "STRING", "enum": [str(v) for v in node["enum"]]}
        elif "const" in node:
            out = {"type": "STRING", "enum": [str(node["const"])]}
        else:
            t = node.get("type", "string")
            if t == "object":
                props = {k: conv(v) for k, v in node.get("properties", {}).items()}
                out = {"type": "OBJECT", "properties": props, "required": list(props)}
            elif t == "array":
                out = {"type": "ARRAY", "items": conv(node.get("items", {"type": "string"}))}
            else:
                out = {"type": _TYPES.get(t, "STRING")}
        if node.get("description"):
            out["description"] = node["description"]
        return out

    return conv(root)
