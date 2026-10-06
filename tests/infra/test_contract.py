"""Tests: contract/protocol.json is well formed, covers the plan, and its generated files are current.

Run on its own: python3 tests/infra/test_contract.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

ROOT = Path(__file__).resolve().parents[2]
SPEC = json.loads((ROOT / "contract" / "protocol.json").read_text())
METHODS = {m["name"]: m for m in SPEC["methods"]}

# docs/daemon-plan.md "What the web UI gets" and "Session control".
PLAN_METHODS = """daemon.status accounts.list agents.list agent.get agent.timeline prompt.list terminal.attach terminal.history
terminal.resize session.start session.wake session.stop session.restart session.park session.input session.keys session.interrupt
prompt.answer input.take input.release inbox.list inbox.mark_read inbox.answer inbox.reply tasks.list task.get task.new task.withdraw
task.cancel task.edit team.get hr.list hr.decide account.set_cap account.login repos.list worktrees.list worktree.diff
worktree.set_status agent.options agent.create schedules.list schedule.add schedule.edit schedule.remove stats.get settings.get
settings.set settings.trust devices.list chat.messages chat.transcript chat.locate chat.send chat.answer chat.dismiss chat.withdraw input.ask device.pair device.redeem device.revoke events.feed whoami hello subscribe""".split()
AGENT_COMMANDS = "task.new task.handoff task.done task.reopen task.progress inbox.list inbox.get say ask push land hook.record agent.set " \
                 "session.wake session.stop session.restart session.park".split()
BARRED_FROM_AGENTS = "session.input session.keys session.interrupt prompt.answer input.take input.release terminal.attach " \
                     "terminal.history terminal.resize chat.send input.ask devices.list device.pair device.revoke settings.set".split()
FOUNDER_ONLY = "schedule.add schedule.edit schedule.remove settings.get settings.set devices.list device.pair device.revoke".split()
ROLES = SPEC["x-roles"]["order"]


def _refs(node):
    if isinstance(node, dict):
        if "$ref" in node:
            yield node["$ref"]
        for v in node.values():
            yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


def test_every_method_the_plan_names_is_in_the_spec() -> None:
    missing = [n for n in PLAN_METHODS if n not in METHODS]
    check("contract: every plan method is present", not missing, str(missing))
    check("contract: method names are unique", len(METHODS) == len(SPEC["methods"]))


def test_roles_follow_the_plan() -> None:
    rank = {r: i for i, r in enumerate(ROLES)}
    check("contract: every method has a known role", all(m["x-role"] in rank for m in METHODS.values()))
    check("contract: agents keep their commands", all(rank[METHODS[n]["x-role"]] <= rank["agent"] for n in AGENT_COMMANDS),
          str([n for n in AGENT_COMMANDS if rank[METHODS[n]["x-role"]] > rank["agent"]]))
    check("contract: person-at-the-terminal methods bar agents", all(rank[METHODS[n]["x-role"]] > rank["agent"] for n in BARRED_FROM_AGENTS))
    check("contract: founder-only methods are founder", all(METHODS[n]["x-role"] == "founder" for n in FOUNDER_ONLY))
    check("contract: only hello and device.redeem are open to anonymous",
          sorted(n for n, m in METHODS.items() if m["x-role"] == "anonymous") == ["device.redeem", "hello"])


def test_every_write_takes_a_request_id_and_reads_do_not() -> None:
    bad = [n for n, m in METHODS.items()
           if m["x-write"] != any(p["name"] == "request_id" and p["required"] for p in m["params"])]
    check("contract: request_id exactly on writes", not bad, str(bad))


def test_every_reference_resolves() -> None:
    schemas, errors = SPEC["components"]["schemas"], SPEC["components"]["errors"]
    bad = []
    for ref in _refs(SPEC):
        kind, name = ref.split("/")[-2:]
        if name not in (schemas if kind == "schemas" else errors):
            bad.append(ref)
    check("contract: every $ref resolves", not bad, str(bad))
    wanted = {"not_found", "refused", "stale_prompt", "daemon_busy", "bad_request", "internal", "daemon_down"}
    check("contract: the error codes the plan names", set(errors) == wanted)


def test_session_gates_are_declared_and_defined() -> None:
    defined = set(SPEC["x-gates"])
    used = {g for m in METHODS.values() for g in m.get("x-gates", [])}
    check("contract: every gate used is defined", used <= defined, str(used - defined))
    inp = set(METHODS["session.input"]["x-gates"])
    check("contract: input passes the wall and screen-blocker gates, keys does not",
          {"wall", "screen_blocker"} <= inp and not {"wall", "screen_blocker"} & set(METHODS["session.keys"]["x-gates"]))
    check("contract: prompt.answer can answer stale_prompt",
          any(e["$ref"].endswith("/stale_prompt") for e in METHODS["prompt.answer"]["errors"]))


def test_every_topic_has_a_snapshot_method_and_the_terminal_channel_is_text() -> None:
    check("contract: topics are the plan's seven plus chat", sorted(SPEC["x-topics"]) ==
          sorted("status accounts agents inbox tasks notifications operations chat".split()))
    check("contract: every topic's snapshot method exists", all(t["snapshot"] in METHODS for t in SPEC["x-topics"].values()))
    check("contract: frames are text with a reserved place for styled rows",
          SPEC["components"]["schemas"]["TerminalSnapshot"]["properties"]["format"]["enum"] == ["text"]
          and "spans" in SPEC["components"]["schemas"]["Row"]["properties"])


def test_the_legacy_verbs_all_map() -> None:
    src = (ROOT / "handline" / "daemon" / "socket_server.py").read_text()
    block = src.split("VERBS = {", 1)[1].split("}", 1)[0]
    verbs = {ln.split('"')[1] for ln in block.splitlines() if ln.strip().startswith('"')} | {"land_authorize"}
    check("contract: every legacy socket verb is mapped", verbs == set(SPEC["x-legacy"]), str(verbs ^ set(SPEC["x-legacy"])))


def test_generated_files_are_current() -> None:
    r = subprocess.run([sys.executable, str(ROOT / "contract" / "generate.py"), "--check"], capture_output=True, text=True)
    check("contract: protocol.md and protocol.ts match protocol.json", r.returncode == 0, r.stderr)


def main() -> int:
    def tests(td):
        run(lambda: test_every_method_the_plan_names_is_in_the_spec())
        run(lambda: test_roles_follow_the_plan())
        run(lambda: test_every_write_takes_a_request_id_and_reads_do_not())
        run(lambda: test_every_reference_resolves())
        run(lambda: test_session_gates_are_declared_and_defined())
        run(lambda: test_every_topic_has_a_snapshot_method_and_the_terminal_channel_is_text())
        run(lambda: test_the_legacy_verbs_all_map())
        run(lambda: test_generated_files_are_current())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
