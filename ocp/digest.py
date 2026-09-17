"""Digest parsing, validation and rendering (design section 5).

The section list is fixed on purpose. With fixed sections a fold becomes
"move items between sections", which is a stable rewrite. Free-form prose makes
the structure wobble on every pass and the rewrite becomes unreliable.

Section headings are the design's, kept verbatim because they are a data format
that agents and the user both read. Only the identifiers here are English.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# (identifier, heading text as it appears in the file)
SECTIONS: list[tuple[str, str]] = [
    ("done", "완료"),
    ("in_progress", "진행중"),
    ("decided", "결정"),
    ("blocked", "막힌 것"),
    ("next", "다음"),
    ("base_candidates", "base 승격 후보"),
]
SECTION_KEYS = [k for k, _ in SECTIONS]
HEADINGS = {k: h for k, h in SECTIONS}
BY_HEADING = {h: k for k, h in SECTIONS}

# Appended by us after every fold, independent of anything a model produced, so
# a failed fold still leaves a resumable handoff (section 5).
FACTS_HEADING = "기계 기록"
LATE_HEADING = "지각 반영"

_H2 = re.compile(r"^##\s+(.+?)\s*$", re.M)
_ITEM = re.compile(r"^\s*[-*]\s+(.*\S)\s*$", re.M)


@dataclass
class Digest:
    date: str = ""
    agent: str = ""
    updated: str = ""
    folded_through: int = 0
    sections: dict[str, list[str]] = field(default_factory=dict)
    facts: str = ""
    late: list[str] = field(default_factory=list)

    def items(self, key: str) -> list[str]:
        return self.sections.get(key, [])


def parse(text: str) -> Digest:
    d = Digest(sections={k: [] for k in SECTION_KEYS})

    head = re.search(r"^#\s+(\S+)\s*·\s*(\S+)", text, re.M)
    if head:
        d.date, d.agent = head.group(1), head.group(2)
    meta = re.search(r"^updated:\s*(.*?)(?:\s*·\s*folded_through:\s*event\s*(\d+))?\s*$",
                     text, re.M)
    if meta:
        d.updated = (meta.group(1) or "").strip()
        d.folded_through = int(meta.group(2) or 0)

    # Split on level-2 headings and bucket the items under each.
    marks = list(_H2.finditer(text))
    for i, m in enumerate(marks):
        heading = m.group(1).strip()
        body = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        if heading in BY_HEADING:
            d.sections[BY_HEADING[heading]] = [x.group(1).strip() for x in _ITEM.finditer(body)]
        elif heading == FACTS_HEADING:
            d.facts = body.strip()
        elif heading == LATE_HEADING:
            d.late = [x.group(1).strip() for x in _ITEM.finditer(body)]
    return d


def render(d: Digest) -> str:
    out = [f"# {d.date} · {d.agent}",
           f"updated: {d.updated} · folded_through: event {d.folded_through}", ""]
    for key, heading in SECTIONS:
        out.append(f"## {heading}")
        items = d.sections.get(key, [])
        out.extend(f"- {i}" for i in items)
        if not items:
            out.append("")
        out.append("")
    if d.late:
        out.append(f"## {LATE_HEADING}")
        out.extend(f"- {i}" for i in d.late)
        out.append("")
    if d.facts:
        out.append(f"## {FACTS_HEADING}")
        out.append(d.facts.strip())
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def validate(text: str, previous: Digest | None,
             header_is_stamped: bool = False) -> tuple[bool, list[str]]:
    """Check a candidate digest before it is allowed to replace a good one.

    Atomic writes only stop a half-written file. They do not stop a fully
    written garbage file from overwriting a good one (section 5), which is what
    these checks are for.
    """
    problems: list[str] = []
    found = {m.group(1).strip() for m in _H2.finditer(text)}
    missing = [h for _, h in SECTIONS if h not in found]
    if missing:
        problems.append(f"missing sections: {', '.join(missing)}")

    cand = parse(text)
    if not cand.agent and not header_is_stamped:
        # The caller that stamps the header does not care what the model wrote
        # there. Rejecting on it throws away good section work, takes the
        # degraded path and leaves the cursor unadvanced, all over a line that
        # was about to be replaced.
        problems.append("header line '# <date> · <agent>' is missing or malformed")

    if previous is not None:
        # Monotonicity: nothing that was already done may disappear. A fold is
        # a destructive rewrite, so losing history is the failure to catch.
        before = {i.strip() for i in previous.items("done")}
        after = {i.strip() for i in cand.items("done")}
        lost = before - after
        if lost:
            preview = "; ".join(sorted(lost)[:3])
            problems.append(f"{len(lost)} completed item(s) disappeared: {preview}")
        if previous.folded_through and cand.folded_through < previous.folded_through:
            problems.append(
                f"folded_through went backwards: {previous.folded_through} -> {cand.folded_through}"
            )
    return (not problems), problems


def carry_forward(previous: Digest, new_date: str) -> Digest:
    """Midnight rollover, with no model involved (section 5).

    Yesterday's 'in progress' would otherwise stay in progress forever. Carry
    in_progress / blocked / next; start 'done' empty for the new day.
    """
    d = Digest(date=new_date, agent=previous.agent, updated="00:00 (rollover)",
               folded_through=previous.folded_through,
               sections={k: [] for k in SECTION_KEYS})
    for key in ("in_progress", "blocked", "next", "base_candidates"):
        d.sections[key] = list(previous.items(key))
    return d
