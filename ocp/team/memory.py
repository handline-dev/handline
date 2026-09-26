"""Per-agent memory: where it lives, its budget, and the move from the shared
folder (docs/memory-design.md, T229/T231).

ocp measures and reports; it never rewrites or deletes an agent's entries.
Enforcing the cap on a write and recording last use are hooks (T230).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from ..infra import paths

INDEX_BYTES = 2048
INDEX_ENTRIES = 20
STALE_DAYS = 28  # founder #1287
TEAM_BYTES = 2048
TEAM_RULES = 10
INDEX_LINE = 100  # an index line is a pointer; the rule lives in the topic file
INDEX = "MEMORY.md"
PROJECT_SUFFIX = "-orcapipe"  # Claude keys a repo folder by its path

# Who owns each file of shared/memory/claude/<orcapipe repo> (design doc,
# "Migrating"). Only agent names are values here; a file that leaves memory is
# in PROMOTED or DROPPED instead, so no agent name can collide with them.
OWNERS = {
    "batch-per-owner": "orcapipe",
    "idle-not-free": "orcapipe",
    "feedback-queue-discipline": "orcapipe",
    "founder-reports-fyi": "orcapipe",
    "feedback-founder-working-style": "orcapipe",
    "auditor-run-suite-before-verdict": "orcapipe.auditor",
    "feedback-testing": "orcapipe.ui",
    "feedback-minimal-ui-tests": "orcapipe.ui",
    "feedback-full-suite-before-review": "orcapipe.ui",
    "feedback-no-tests-until-ok": "orcapipe.ui",
}
PROMOTED = {"ocp-cli-habits", "ocp-resolve-quoting"}  # to AGENTS.md
DROPPED = {"project-ocp-home-wipe"}
# Files that say one thing between them; the owner merges them at confirmation,
# ocp copies each verbatim.
MERGE_GROUPS = [{"feedback-testing", "feedback-minimal-ui-tests",
                 "feedback-full-suite-before-review"}]


def memory_dir(agent: str) -> Path:
    return paths.agent_dir(agent) / "memory"


def use_file(agent: str) -> Path:
    """`{path relative to the memory dir: last used, unix seconds}`, written by
    the daemon's hook. Outside the memory dir so it is never loaded or counted."""
    return paths.agent_dir(agent) / "memory-use.json"


def index_entries(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("- [")]


def _linked(text: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"\]\(([^)\s]+)\)", text)}


def _read_use(agent: str) -> dict:
    try:
        data = json.loads(use_file(agent).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def status(agent: str, now: float | None = None) -> dict:
    """Bytes and entries against budget, topic files the index does not name,
    and files unused for STALE_DAYS (mtime stands in for a file never read)."""
    now = time.time() if now is None else now
    d = memory_dir(agent)
    out = {"agent": agent, "exists": d.is_dir(), "bytes": 0, "entries": 0,
           "over": False, "unindexed": [], "stale": []}
    if not d.is_dir():
        return out
    idx = d / INDEX
    text = idx.read_text(errors="replace") if idx.is_file() else ""
    out["bytes"] = len(text.encode())
    out["entries"] = len(index_entries(text))
    out["over"] = out["bytes"] > INDEX_BYTES or out["entries"] > INDEX_ENTRIES
    named = _linked(text)
    use = _read_use(agent)
    for f in sorted(d.rglob("*.md")):
        rel = f.relative_to(d).as_posix()
        if rel == INDEX:
            continue
        if rel not in named:
            out["unindexed"].append(rel)
        try:
            last = float(use.get(rel) or f.stat().st_mtime)
        except (OSError, TypeError, ValueError):
            continue
        if now - last > STALE_DAYS * 86400:
            out["stale"].append(rel)
    return out


def summary(st: dict) -> str:
    if not st["exists"]:
        return f"{st['agent']}: no memory yet"
    line = (f"{st['agent']}: {st['bytes']}/{INDEX_BYTES} bytes, "
            f"{st['entries']}/{INDEX_ENTRIES} entries")
    if st["over"]:
        line += " - OVER BUDGET, merge or delete before adding"
    if st["unindexed"]:
        line += f"; not in the index: {', '.join(st['unindexed'])}"
    if st["stale"]:
        line += f"; unused {STALE_DAYS}+ days: {', '.join(st['stale'])}"
    return line


def _body(path: Path) -> str:
    text = path.read_text(errors="replace")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    return " ".join(text.lower().split())


def promotion_candidates(only: str | None = None) -> list[tuple[str, list[str]]]:
    """Topic files whose text is the same in more than one agent's memory:
    a lesson every agent needs belongs in AGENTS.md or ROLE.md. With `only`,
    just the groups that agent is in."""
    seen: dict[str, list[str]] = {}
    for agent in paths.list_agents():
        d = memory_dir(agent)
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.md")):
            if f.name == INDEX or not f.is_file():
                continue
            body = _body(f)
            if body:
                key = hashlib.sha1(body.encode()).hexdigest()
                seen.setdefault(key, []).append(f"{agent}/{f.relative_to(d).as_posix()}")
    return [(k, v) for k, v in seen.items() if len({x.split("/")[0] for x in v}) > 1
            and (only is None or any(x.startswith(only + "/") for x in v))]


# ------------------------------------------------------------- team layer
def team_file(team: str) -> Path:
    return paths.home() / "shared" / "team" / f"{team}.md"


def team_layers(agent: str, teams: dict[str, list[str]]) -> list[tuple[str, str]]:
    """(team, text) for every team this agent is in that has a rules file."""
    out = []
    for name, members in sorted(teams.items()):
        f = team_file(name)
        if agent in (members or []) and f.is_file():
            text = f.read_text().strip()
            if text:
                out.append((name, text))
    return out


def team_problems(teams: dict[str, list[str]]) -> list[str]:
    out = []
    for name in sorted(teams):
        f = team_file(name)
        if not f.is_file():
            continue
        text = f.read_text()
        rules = sum(1 for ln in text.splitlines() if ln.startswith("- "))
        if len(text.encode()) > TEAM_BYTES or rules > TEAM_RULES:
            out.append(f"team {name}: {len(text.encode())}/{TEAM_BYTES} bytes, "
                       f"{rules}/{TEAM_RULES} rules")
    return out


# --------------------------------------------------------------- migration
def _front(path: Path) -> dict:
    text = path.read_text(errors="replace")
    meta = {}
    if text.startswith("---"):
        for ln in text.split("---", 2)[1].splitlines():
            m = re.match(r"(name|description):\s*(.*)", ln)
            if m:
                meta[m.group(1)] = m.group(2).strip()
    return meta


def _index_line(path: Path) -> str:
    meta = _front(path)
    hook = meta.get("description", "")
    name = meta.get("name") or path.stem
    head = f"- [{name}]({path.name})"
    if len(head) > INDEX_LINE:
        # The link must stay whole: shorten the title, never cut inside the parens.
        head = f"- [{name[:max(1, INDEX_LINE - len(path.name) - 7)]}…]({path.name})"
        return head
    if not hook:
        return head
    line = f"{head} — {hook}"
    return line if len(line) <= INDEX_LINE else line[:INDEX_LINE - 1] + "…"


def shared_sources() -> list[Path]:
    """The orcapipe repo's shared folder only: OWNERS names its files, and a
    file of the same name in another repo's folder is not the same fact."""
    root = paths.home() / "shared" / "memory" / "claude"
    if not root.is_dir():
        return []
    return sorted(f for d in root.iterdir() if d.is_dir() and d.name.endswith(PROJECT_SUFFIX)
                  for f in sorted(d.glob("*.md")) if f.name != INDEX)


def plan_migration() -> list[dict]:
    """One row per shared topic file: where it goes and what will happen.

    action: copy (into an existing agent), merge (copy, and the owner merges the
    group), promote / delete (leaves memory, nothing copied), no-owner (not in
    the table; left for a human), no-agent (the owner is not registered)."""
    known = set(paths.list_agents())
    rows = []
    for f in shared_sources():
        to = OWNERS.get(f.stem)
        if f.stem in PROMOTED:
            action, to = "promote", "AGENTS.md"
        elif f.stem in DROPPED:
            action = "delete"
        elif to is None:
            action = "no-owner"
        elif to not in known:
            action = "no-agent"
        elif any(f.stem in g for g in MERGE_GROUPS):
            action = "merge"
        else:
            action = "copy"
        rows.append({"file": f, "to": to, "action": action})
    return rows


def apply_migration(rows: list[dict]) -> list[str]:
    """Copy each `copy`/`merge` file verbatim into its owner's memory dir and
    add an index line for it. Never overwrites a file, never touches
    shared/memory: the shared folder stays until each agent has confirmed."""
    done = []
    for r in rows:
        if r["action"] not in ("copy", "merge"):
            continue
        d = memory_dir(r["to"])
        d.mkdir(parents=True, exist_ok=True)
        dest = d / r["file"].name
        if dest.exists():
            continue
        dest.write_bytes(r["file"].read_bytes())
        idx = d / INDEX
        text = idx.read_text() if idx.is_file() else ""
        if text and not text.endswith("\n"):
            text += "\n"
        idx.write_text(text + _index_line(dest) + "\n")
        done.append(f"{r['file'].name} -> {r['to']}")
    return done
