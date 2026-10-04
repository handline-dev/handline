"""What waits on what, as a tree (founder #2228/#2229)."""
from __future__ import annotations


def task_tree(rows: list[dict], shown: set[int]) -> list[dict]:
    """The `task_rows` dicts in `rows` (already in the order siblings should take) as tree lines,
    one dict per line: `id`, `prefix` (the `└─ ` and `│  ` drawn before the task), `dim` (the task
    is not in `shown` but holds up one that is, so the chain stays whole) and `again` (the task
    already stands higher up; this line only points there, with nothing under it).

    A task hangs under each task it is blocked by, and under its parent task. The block a child
    puts on its parent (T294) is left out, since the parent already sits above the child. Roots are
    the tasks waiting on no other; a task reached from several places is drawn in full the first
    time only. A cycle has no root, so its first task in `rows` takes that place, and the walk
    ends where it comes back to a task already drawn."""
    by_id = {r["id"]: r for r in rows}
    above: dict[int, list[int]] = {t: [] for t in by_id}     # the tasks each one hangs under
    below: dict[int, list[int]] = {t: [] for t in by_id}
    for r in rows:
        ups = [b for b in r["blocked_by"] if b in by_id and by_id[b].get("parent_task") != r["id"]]
        if r.get("parent_task") in by_id and r["parent_task"] not in ups:
            ups.append(r["parent_task"])
        for u in ups:
            if u != r["id"]:
                above[r["id"]].append(u)
                below[u].append(r["id"])
    keep: set[int] = set()
    todo = [t for t in shown if t in by_id]
    while todo:
        t = todo.pop()
        if t not in keep:
            keep.add(t)
            todo.extend(above[t])
    order = {r["id"]: i for i, r in enumerate(rows)}
    below = {t: sorted((c for c in cs if c in keep), key=order.get) for t, cs in below.items()}
    out: list[dict] = []
    drawn: set[int] = set()

    def walk(tid: int, lead: str, last: bool | None) -> None:
        stem = "" if last is None else "└─ " if last else "├─ "
        again = tid in drawn
        out.append({"id": tid, "prefix": lead + stem, "dim": tid not in shown, "again": again})
        if again:
            return
        drawn.add(tid)
        kids = below[tid]
        for i, c in enumerate(kids):
            walk(c, lead + ("" if last is None else "   " if last else "│  "), i == len(kids) - 1)

    for r in rows:
        if r["id"] in keep and not above[r["id"]]:
            walk(r["id"], "", None)
    for r in rows:      # what is left hangs in a cycle
        if r["id"] in keep and r["id"] not in drawn:
            walk(r["id"], "", None)
    return out
