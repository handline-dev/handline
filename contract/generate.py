#!/usr/bin/env python3
"""Generate contract/protocol.md and contract/protocol.ts from contract/protocol.json.

  python3 contract/generate.py            write both files
  python3 contract/generate.py --check    exit 1 if either file is stale (tests/infra/test_contract.py runs this)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = HERE / "protocol.json"
HEADER = "Generated from contract/protocol.json by contract/generate.py; do not edit."


def pascal(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[._]", name))


def ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[1]


def ts(schema: dict, indent: int = 0) -> str:
    """A JSON Schema as a TypeScript type."""
    if "$ref" in schema:
        return ref_name(schema["$ref"])
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    for key, joiner in (("oneOf", " | "), ("anyOf", " | "), ("allOf", " & ")):
        if key in schema:
            return joiner.join(_wrap(ts(s, indent)) for s in schema[key])
    t = schema.get("type")
    if t == "string":
        return "string"
    if t in ("integer", "number"):
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    if t == "array":
        return _wrap(ts(schema["items"], indent)) + "[]"
    if t == "object":
        props = schema.get("properties")
        extra = schema.get("additionalProperties")
        if not props:
            return "{ [key: string]: %s }" % (ts(extra, indent) if isinstance(extra, dict) else "unknown") if extra is not False else "{}"
        req = set(schema.get("required", []))
        pad = "  " * (indent + 1)
        lines = []
        for k, v in props.items():
            if v.get("description"):
                lines.append(f"{pad}/** {v['description']} */")
            lines.append(f"{pad}{json.dumps(k) if not re.fullmatch(r'[A-Za-z_]\w*', k) else k}{'' if k in req else '?'}: {ts(v, indent + 1)};")
        if extra:
            lines.append(f"{pad}[key: string]: unknown;")
        return "{\n" + "\n".join(lines) + "\n" + "  " * indent + "}"
    return "unknown"


def _wrap(t: str) -> str:
    return f"({t})" if (" | " in t or " & " in t) and not t.startswith("{") else t


def params_type(m: dict) -> str:
    return ts({"type": "object", "properties": {p["name"]: p["schema"] for p in m["params"]},
               "required": [p["name"] for p in m["params"] if p["required"]]})


def gen_ts(spec: dict) -> str:
    out = [f"// {HEADER}", "/* eslint-disable */", ""]
    for name, s in spec["components"]["schemas"].items():
        if s.get("description"):
            out.append(f"/** {s['description']} */")
        out.append(f"export type {name} = {ts(s)};\n")
    out.append("export interface Methods {")
    for m in spec["methods"]:
        out.append(f"  {json.dumps(m['name'])}: {{ params: {params_type(m)}; result: {ts(m['result']['schema'], 1)} }};")
    out += ["}", "", "export type MethodName = keyof Methods;", ""]
    out.append("export const METHODS: { [K in MethodName]: { role: Role; callers: Role[] | null; write: boolean; since: number } } = {")
    for m in spec["methods"]:
        out.append(f"  {json.dumps(m['name'])}: {{ role: {json.dumps(m['x-role'])}, callers: {json.dumps(m.get('x-callers'))}, write: {json.dumps(m['x-write'])}, since: {m['x-since']} }},")
    out += ["};", "", f"export const PROTOCOL = {spec['x-protocol']};", ""]
    codes = spec["components"]["errors"]
    out.append("export type ErrorCode = " + " | ".join(json.dumps(c) for c in codes) + ";")
    out.append("export const RPC_CODES: { [K in ErrorCode]: number | null } = {")
    out += [f"  {c}: {json.dumps(e['code'])}," for c, e in codes.items()]
    out += ["};", "", "export interface Notifications {"]
    for n, d in spec["x-notifications"].items():
        out.append(f"  {n}: {ts(d['params']) if isinstance(d['params'], dict) else d['params']};")
    out += ["}", "", "/** The state words the daemon sends; an open set, so a client keeps a fallback. */",
            "export const STATE_WORDS = " + json.dumps(list(spec["x-state-words"]["words"])) + " as const;", "",
            "export const ROLE_ORDER: Role[] = " + json.dumps(spec["x-roles"]["order"]) + ";", ""]
    return "\n".join(out)


def short(schema: dict) -> str:
    """A result type for a table cell: named types by name, inline objects as {...}."""
    if "$ref" in schema:
        return ref_name(schema["$ref"])
    for key, joiner in (("allOf", " & "), ("oneOf", " | "), ("anyOf", " | ")):
        if key in schema:
            return joiner.join(short(s) for s in schema[key])
    if schema.get("type") == "object":
        return "{" + ", ".join(schema.get("properties", {})) + "}" if schema.get("properties") else "object"
    return ts(schema).split("\n")[0]


def cell(t: str) -> str:
    return t.replace("|", "\\|").replace("\n", " ")


def gen_md(spec: dict) -> str:
    fr = spec["x-framing"]
    o = [f"<!-- {HEADER} -->", f"# {spec['info']['title']} (protocol {spec['x-protocol']})", "", spec["info"]["description"], "",
         "## Framing", ""]
    o += [f"- **Transport:** {'; '.join(fr['transport'])}."] + [f"- **{k.replace('_', ' ').capitalize()}:** {fr[k]}" for k in ("reach", "lines", "ids", "concurrency", "network", "params", "legacy", "request_id")]
    o += ["", "## Handshake and versions", "", spec["x-handshake"], "", "## Roles", "",
          f"Order: {' < '.join(spec['x-roles']['order'])}. {spec['x-roles']['rule']}", "", "## Errors", "",
          "Every error is a JSON-RPC error whose `data.code` is the stable string. A refusal carries the reason; fail-closed is `refused`, never a silent default.", "",
          "| Code | RPC code | data |", "|---|---|---|"]
    o += [f"| `{c}` | {e['code'] if e['code'] is not None else 'none (client side)'} | {cell(e['data'])} |" for c, e in spec["components"]["errors"].items()]
    o += ["", "## Gates", "", "| Gate | Refuses when |", "|---|---|"] + [f"| `{g}` | {cell(t)} |" for g, t in spec["x-gates"].items()]
    o += ["", "## Methods", "", "A method marked write takes a required `request_id`.", "",
          "| Method | Role | Write | Params | Result | Errors | Gates |", "|---|---|---|---|---|---|---|"]
    for m in spec["methods"]:
        ps = ", ".join(f"`{p['name']}{'' if p['required'] else '?'}`" for p in m["params"]) or "none"
        errs = ", ".join(f"`{ref_name(e['$ref'])}`" for e in m.get("errors", [])) or ""
        o.append(f"| `{m['name']}` | {m['x-role']}{' (only ' + ', '.join(m['x-callers']) + ')' if m.get('x-callers') else ''} | {'yes' if m['x-write'] else ''} | {ps} | `{cell(short(m['result']['schema']))}` | {errs} | {', '.join(m.get('x-gates', []))} |")
    o += ["", "Per-method summaries and field types are in `protocol.json` and `protocol.ts`.", ""]
    for m in spec["methods"]:
        o += [f"### `{m['name']}`", "", m["summary"] + (" " + m["description"] if m.get("description") else ""), ""]
    ident = spec["x-identity"]
    o += ["## Who the caller is", "", ident["rule"], "", "- **Sender:** " + ident["sender"], "- **Ownership:** " + ident["ownership"], "",
          "## Agent commands", "", "Every `hl` command an agent runs today and the method that serves it.", "", "| Command | Method |", "|---|---|"]
    o += [f"| `{c}` | `{m}` |" for c, m in spec["x-agent-commands"].items()]
    d = spec["x-dropped-from-agents"]
    o += ["", "## Dropped from agents on purpose", "", d["note"], ""]
    o += [f"- **{k.replace('_', ' ').capitalize()}:** " + "; ".join(f"`{x}`" for x in v) for k, v in d.items() if k != "note"]
    o += ["", spec["x-note-topics"], ""]
    o += ["## Topics", "", "| Topic | Event types (data) | Snapshot method |", "|---|---|---|"]
    for t, d in spec["x-topics"].items():
        o.append(f"| `{t}` | {', '.join(f'`{k}` ({v})' for k, v in d['data'].items())} | `{d['snapshot']}` |")
    sw = spec["x-state-words"]
    o += ["", "## State words", "", sw["rule"], "", "| Word | Meaning |", "|---|---|"] + [f"| `{w}` | {cell(m)} |" for w, m in sw["words"].items()]
    o += ["", "What the daemon sends for each of its own states:", "", "| Daemon state | Word |", "|---|---|"] + [f"| {cell(k)} | `{v}` |" for k, v in sw["from_daemon"].items()]
    o += ["", sw["pending"]]
    o += ["", "## Notifications", ""]
    for n, d in spec["x-notifications"].items():
        o.append(f"- `{n}`: {d.get('note', '')} Params: `{ts(d['params']).replace(chr(10), ' ') if isinstance(d['params'], dict) else d['params']}`")
    o += ["", "## Terminal channel", "", spec["x-terminal"], "", "### Format bytes", ""] + [f"- **{k.replace('_', ' ').capitalize()}.** {v}" for k, v in spec["x-terminal-bytes"].items()]
    o += ["", "### Format styled", ""] + [f"- **{k.replace('_', ' ').capitalize()}.** {v}" for k, v in spec["x-terminal-styled"].items()]
    o += ["", "## Transcript stream", ""] + [f"- **{k.replace('_', ' ').capitalize()}.** {v}" for k, v in spec["x-transcript"].items()]
    o += ["", "## Legacy verbs", "", "| Legacy verb | Method |", "|---|---|"]
    o += [f"| `{k}` | {f'`{v}`' if v else 'none'} |" for k, v in spec["x-legacy"].items()]
    return "\n".join(o) + "\n"


def outputs() -> dict[Path, str]:
    spec = json.loads(SPEC.read_text())
    return {HERE / "protocol.ts": gen_ts(spec), HERE / "protocol.md": gen_md(spec)}


def main(argv: list[str]) -> int:
    stale = [p for p, text in outputs().items() if not p.exists() or p.read_text() != text]
    if "--check" in argv:
        for p in stale:
            print(f"stale: {p.name}; run python3 contract/generate.py", file=sys.stderr)
        return 1 if stale else 0
    for p, text in outputs().items():
        p.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
