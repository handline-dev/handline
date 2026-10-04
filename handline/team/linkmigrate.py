"""Moving a team.yaml written before names gave the links (`team.DEFAULT_LINKS`) onto them.

Two things change in the file and nothing else: an edge the name links now give, with the
same relation, is dropped; and each pair of team managers that used to reach one another by a
built-in rule gets the edge it needs, written on purpose. Comments and every other edge stay.
"""
from __future__ import annotations

from ..infra.yamlcompat import ConfigError, load
from . import hr, team


def _edges_of(text: str):
    """(line index, edge) for each one-line edge of the file."""
    lines = text.splitlines()
    head, end = hr._block(lines, "edges:")
    out = []
    for i, _frm, _to in hr._edge_lines(lines, head, end):
        e = (load("e: " + lines[i].strip()[1:].strip()) or {}).get("e")
        out.append((i, e))
    return out


def _covered(tm: team.Team, e: dict) -> bool:
    """Does the name link give exactly what this edge does?"""
    frm, to = str(e["from"]), str(e["to"])
    if e.get("kind") or e.get("response", "async") != "async" or "*" in frm:
        return False
    if to.endswith(".*"):
        members = [m for m in tm.members(to[:-2]) if m != frm]
        return bool(members) and all(_gives(tm, e, frm, m) for m in members)
    return "*" not in to and _gives(tm, e, frm, to)


def _gives(tm: team.Team, e: dict, frm: str, to: str) -> bool:
    d = tm.default_link(frm, to)
    return d is not None and e.get("relation") in (None, d.relation) and d.kind is None


def _without(edges: list[dict], dropped: list[dict]) -> list[dict]:
    """`edges` less one equal edge for each of `dropped` (the dropped ones are parsed again from
    their lines, so they are equal to the table's edges, not the same objects)."""
    left = list(edges)
    for e in dropped:
        left.remove(e)
    return left


def plan(text: str) -> tuple[list[tuple[int, dict]], list[tuple[str, str]]]:
    """(edges to drop as (line, edge), pairs of managers to link) for the file's text."""
    data = load(text)
    tm = team.Team(data)
    drop = [(i, e) for i, e in _edges_of(text) if _covered(tm, e)]
    rest = team.Team({**data, "edges": _without(data.get("edges") or [], [e for _, e in drop])})
    mgrs = sorted(rest.managers())
    add = [(a, b) for a in mgrs for b in mgrs if a != b and not rest.route(a, b).allowed]
    return drop, add


def edit(text: str) -> str:
    drop, add = plan(text)
    gone = {i for i, _ in drop}
    text = "\n".join(l for i, l in enumerate(text.splitlines()) if i not in gone) + "\n"
    for a, b in add:
        text = hr._link_text(text, a, b, "async", "peer")
    return text


def describe(text: str) -> list[str]:
    drop, add = plan(text)
    return ([f"drop  {e['from']} -> {e['to']}"
             + (f" ({e['relation']})" if e.get("relation") else "") for _, e in drop]
            + [f"add   {a} -> {b} (peer)" for a, b in add])


def migrate(apply: bool) -> tuple[bool, str]:
    """The plan for the live team.yaml, and with `apply` the file written from it (founder only;
    the previous file is kept beside it). Nothing a team could do before is refused after: the
    result is checked pair by pair against the table as it stood."""
    from ..infra import paths, sealed
    p = paths.team_file()
    if not p.exists():
        return False, f"{p} does not exist"
    text = p.read_text()
    try:
        lines = describe(text)
    except ConfigError as e:
        return False, str(e)
    if not lines:
        return True, "team.yaml already rests on the name links - nothing to change"
    if not apply:
        return True, "\n".join(lines) + "\nnothing written; `hl hr links --apply` writes it"
    before = team.Team(load(text))
    names = sorted(set(before.agents) | {a for m in before.teams.values() for a in m} | {team.HUMAN})

    def verify(after: team.Team) -> None:
        for a in names:
            for b in names:
                if a != b and before.known(b) and after.known(b):
                    was, now = before.route(a, b), after.route(a, b)
                    hr._need(not (was.allowed and not now.allowed),
                             f"{a} -> {b} would stop working")

    ok, said = hr._edit_team_yaml(edit, verify=verify)
    if not ok:
        return False, said
    return True, ("\n".join(lines) + "\n" + said + "\nA request queued before this, not yet delivered, "
                  "still carries the old relation and is refused at delivery: send it again.")
