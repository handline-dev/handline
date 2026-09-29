"""The wait-for graph over `task_blocks` (T296): what can never finish, told to the human once.

A task waits on the tasks in `task_blocks` (a blocked-by, or the block a child task gives its
parent). Two shapes are stuck for good, and both are read from the db every tick, not caught
at the moment they arise:

- a cycle: `add_block` refuses one, but a merge or a reopen can still close a loop;
- a wait on a blocker that was discarded, or closed without being done. It stops blocking
  (`db.task_blockers` filters it), so the waiter quietly proceeds without what it waited for.

Each goes to the founder once, as an escalation, and is remembered by a cursor until the shape
is gone. Nothing is fixed and no agent is told: which edge to drop is the founder's call.
"""
from __future__ import annotations

from .. import db
from ..team import ask as askmod

_PREFIX = "waitfor:"


def _open(row) -> bool:
    return row["closed_by"] is None and row["done_evidence"] is None


def _cycles(edges: dict[int, list[int]]) -> list[tuple[int, ...]]:
    """The strongly connected groups of more than one task (or one waiting on itself), each as a
    sorted tuple of task ids. Tarjan's, iterative so a long chain cannot hit the recursion limit."""
    index, low, on, stack, out = {}, {}, set(), [], []
    counter = 0
    for root in sorted(edges):
        if root in index:
            continue
        work = [(root, iter(edges.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            node, it = work[-1]
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(edges.get(nxt, ()))))
                    break
                if nxt in on:
                    low[node] = min(low[node], index[nxt])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    group = []
                    while True:
                        member = stack.pop()
                        on.discard(member)
                        group.append(member)
                        if member == node:
                            break
                    if len(group) > 1 or node in edges.get(node, ()):
                        out.append(tuple(sorted(group)))
    return out


def _ring(edges: dict[int, list[int]], group: tuple[int, ...]) -> list[int]:
    """One loop through the group's smallest task, in the order the waits go: the shortest way
    from it back to it along waits inside the group."""
    start, inside = group[0], set(group)
    queue, came = [start], {start: None}
    while queue:
        node = queue.pop(0)
        for nxt in edges.get(node, ()):
            if nxt == start:
                ring = [node]
                while came[ring[-1]] is not None:
                    ring.append(came[ring[-1]])
                return ring[::-1]
            if nxt in inside and nxt not in came:
                came[nxt] = node
                queue.append(nxt)
    return list(group)


def stuck_waits(con) -> dict[str, tuple[int, str]]:
    """{fingerprint: (task id to name the escalation after, text)} for every stuck wait now."""
    tasks = {t["id"]: t for t in db.all_tasks(con)}
    rows = con.execute("SELECT task_id, blocks_on FROM task_blocks ORDER BY task_id, blocks_on").fetchall()
    edges: dict[int, list[int]] = {}
    found: dict[str, tuple[int, str]] = {}
    for r in rows:
        waiter, blocker = tasks.get(r["task_id"]), tasks.get(r["blocks_on"])
        if waiter is None or not _open(waiter):
            continue
        if blocker is None or (blocker["closed_by"] is not None and blocker["done_evidence"] is None):
            gone = "does not exist" if blocker is None else (
                f"was discarded by {blocker['discarded_by']}" if blocker["discarded_by"]
                else f"was closed by {blocker['closed_by']} without being done")
            found[f"gone:{waiter['id']}>{r['blocks_on']}"] = (waiter["id"], (
                f"T{waiter['id']} ({waiter['owner']}) waited on T{r['blocks_on']}, which {gone}. "
                f"That wait no longer holds it back, but what it waited for never happened - "
                f"check whether T{waiter['id']} can go on, or say what it needs instead."))
        elif _open(blocker):
            edges.setdefault(waiter["id"], []).append(blocker["id"])
    for group in _cycles(edges):
        ring = _ring(edges, group)
        names = " -> ".join(f"T{t} ({tasks[t]['owner']})" for t in [*ring, ring[0]])
        more = [t for t in group if t not in ring]
        found["cycle:" + "-".join(map(str, group))] = (group[0], (
            f"These tasks wait on each other and none can finish: {names}"
            + (f", and T{', T'.join(map(str, more))} in the same tangle" if more else "")
            + ". Drop one wait (`ocp task edit T<n> --unblock T<m>`, T<n> being the one that waits) "
              "or close one of them."))
    return found


def sweep(con, log, now: int) -> None:
    """Escalate each stuck wait once; forget the ones that are gone."""
    current = stuck_waits(con)
    told = {r["name"] for r in con.execute(
        "SELECT name FROM cursors WHERE name LIKE ?", (_PREFIX + "%",)).fetchall()}
    for fp, (tid, text) in current.items():
        if _PREFIX + fp in told:
            continue
        task = db.get_task(con, tid)
        who = next((o for o in (task["owner"], task["opened_by"]) if o != db.HUMAN), "orcapipe.daemon")
        askmod.escalate(con, who, f"[wait-for] {text}", "wait-for graph")
        db.set_cursor(con, _PREFIX + fp, 1)
        log(f"  wait-for: {fp} escalated to the founder")
    for name in told:
        if name[len(_PREFIX):] not in current:
            db.clear_cursor(con, name)
