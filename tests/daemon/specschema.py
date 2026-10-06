"""The part of JSON Schema the spec uses, to check a result against the schema contract/protocol.json gives its method (no jsonschema here)."""
from __future__ import annotations

import json
from pathlib import Path

SPEC = json.loads((Path(__file__).resolve().parent.parent.parent / "contract" / "protocol.json").read_text())
SCHEMAS = SPEC["components"]["schemas"]
RESULT = {m["name"]: m["result"]["schema"] for m in SPEC["methods"]}


def _invalid(value, schema, path="result") -> str | None:
    """Why `value` is not what `schema` says, or None: the part of JSON Schema the spec uses."""
    if "$ref" in schema:
        return _invalid(value, SCHEMAS[schema["$ref"].rsplit("/", 1)[1]], path)
    if "oneOf" in schema:
        errs = [_invalid(value, s, path) for s in schema["oneOf"]]
        return None if sum(e is None for e in errs) == 1 else f"{path}: not exactly one of {errs}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: {value!r} is not in {schema['enum']}"
    t = schema.get("type")
    kinds = {"string": str, "boolean": bool, "object": dict, "array": list, "null": type(None)}
    if t == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif t == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif t:
        ok = isinstance(value, kinds[t])
    else:
        ok = True
    if not ok:
        return f"{path}: {value!r} is not {t}"
    if t in ("integer", "number") and "minimum" in schema and value < schema["minimum"]:
        return f"{path}: {value} is below {schema['minimum']}"
    if t == "array":
        return next((e for i, v in enumerate(value) if (e := _invalid(v, schema["items"], f"{path}[{i}]"))), None) if "items" in schema else None
    if t == "object":
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                return f"{path}: {k} is missing"
        for k, v in value.items():
            sub = props.get(k) or (schema["additionalProperties"] if isinstance(schema.get("additionalProperties"), dict) else None)
            if sub is None and (k not in props and schema.get("additionalProperties") is False):
                return f"{path}: {k} is not a property"
            if sub is not None and (e := _invalid(v, sub, f"{path}.{k}")):
                return e
    return None

