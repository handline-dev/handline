"""`ocp memory status|migrate` (docs/memory-design.md)."""
from __future__ import annotations

from ..infra import paths
from ..team import memory


def cmd_memory_status(args) -> int:
    agents = [args.agent] if args.agent else paths.list_agents()
    if args.agent and args.agent not in paths.list_agents():
        print(f"no agent named {args.agent}")
        return 1
    for a in agents:
        print(memory.summary(memory.status(a)))
    for _key, files in memory.promotion_candidates(args.agent):
        print("same text in more than one agent, a candidate for AGENTS.md or ROLE.md: "
              + ", ".join(files))
    return 0


def cmd_memory_migrate(args) -> int:
    rows = memory.plan_migration()
    if not rows:
        print("nothing under shared/memory/claude to migrate")
        return 0
    say = {"copy": "copy to", "merge": "copy to (owner merges the group)",
           "promote": "leaves memory, goes to", "delete": "leaves memory:",
           "no-owner": "has no owner in the table",
           "long-name": "not copied: the file name is too long for an index line:", "no-agent": "owner is not registered:"}
    for r in rows:
        print(f"{r['file'].parent.name}/{r['file'].name}: {say[r['action']]} "
              f"{r['to'] or ''}".rstrip())
    if not args.apply:
        print("\ndry run: nothing written. `ocp memory migrate --apply` copies. "
              "shared/memory is never touched.")
        return 0
    done = memory.apply_migration(rows)
    print("\n" + ("\n".join(done) if done else "nothing to copy (already there)"))
    return 0
