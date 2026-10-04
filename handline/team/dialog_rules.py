"""Standing answers to a vendor's blocking prompts, on the founder's own say-so.

A rule is {vendor, title, label}: for a prompt with exactly this title, choose
the option whose label is exactly this. Never the option's number - a vendor
reorders its options, and a number would then choose something else. Rules
live in the protected store (infra/protected.py), so only the founder's session
can add or remove one; an agent's session gets a refusal, not a raised error.
"""
from __future__ import annotations

import json

from ..infra import protected, textsafe

_NS = "dialog_rules"
_NAME = "rules.json"
_KEYS = ("vendor", "title", "label")


def list_rules() -> list[dict]:
    """Every rule, as {"vendor", "title", "label"}."""
    raw = protected.get(_NS, _NAME)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [{k: r[k] for k in _KEYS} for r in data
            if isinstance(r, dict) and all(isinstance(r.get(k), str) and r[k] for k in _KEYS)]


def _problem(vendor: str, title: str, label: str) -> str | None:
    for name, value in (("vendor", vendor), ("title", title), ("label", label)):
        if not isinstance(value, str) or not value.strip():
            return f"a rule needs a {name}"
        if textsafe.has_unsafe(value):
            return f"the {name} holds a control character"
    return None


def add(vendor: str, title: str, label: str) -> tuple[bool, str]:
    bad = _problem(vendor, title, label)
    if bad:
        return False, bad
    rows = list_rules()
    rule = {"vendor": vendor, "title": title, "label": label}
    if rule in rows:
        return True, f"already a rule: {vendor} '{title}' -> '{label}'"
    if any(r["vendor"] == vendor and r["title"] == title for r in rows):
        return False, (f"{vendor} '{title}' already has a rule; remove it before choosing "
                       f"another label")
    try:
        protected.put(_NS, _NAME, json.dumps(rows + [rule], indent=2))
    except PermissionError as e:
        return False, str(e)
    return True, f"{vendor} '{title}' will be answered '{label}'"


def remove(vendor: str, title: str, label: str) -> tuple[bool, str]:
    rows = list_rules()
    kept = [r for r in rows
            if (r["vendor"], r["title"], r["label"]) != (vendor, title, label)]
    if len(kept) == len(rows):
        return False, f"no rule: {vendor} '{title}' -> '{label}'"
    try:
        protected.put(_NS, _NAME, json.dumps(kept, indent=2))
    except PermissionError as e:
        return False, str(e)
    return True, f"{vendor} '{title}' is no longer answered by rule"


def match(vendor: str, title: str, options: list[tuple[int, str]]) -> int | None:
    """The option number whose label exactly equals the rule for this vendor and
    title, or None: no rule, no such label among the options, or two options
    with that label (which one is not knowable)."""
    for r in list_rules():
        if r["vendor"] == vendor and r["title"] == title:
            hits = [n for n, label in options if label == r["label"]]
            return hits[0] if len(hits) == 1 else None
    return None
