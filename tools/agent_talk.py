#!/usr/bin/env python3
"""How agents should talk to each other: one toy job for three haiku agents on a test daemon of its own, under one design.

  HANDLINE_LIVE=1 python3 tools/agent_talk.py --design A|B|N [--runs 3] [--json]

The job: the founder gives talk.lead a task to get the secret word each builder (talk.a, talk.b) keeps in secret.txt in its own
checkout, and to report both words to the founder. Only the builders can read their files, so the lead has to ask them.
- A: today: hl say and tasks between agents; the lead reports with hl say human.
- B: tasks only: the lead hands out tasks, a builder answers with hl task done evidence, the lead closes the founder's task with
  both words. hl say is refused at its permission prompt.
- N: as B between agents, but the lead reports to the founder in plain text in its own session (native founder talk; the run
  reads it from the transcript topic). hl say is refused.
Plain-text @name lines picked up by the daemon are not run: faking them would deliver the line as the founder's message.

Each run counts, from the event stream (chat, tasks, every agent's transcript):
- wrong sends: a message or task between agents that the job has no edge for, or an hl say tried where it is refused;
- double reports: a word reaching the lead, or a report reaching the founder, more than once;
- lost: a word missing from every report that reached the founder (both, when none did in the time limit);
- stray text: assistant text in a session that would reach the founder if all session text did (in N, the final itself is not
  counted), and of those the echoes: text in the minute after the same agent sent something, naming the other agent or a word;
- tokens and cost from each turn_end, and the time from the founder's task to the final.
Spends tokens on the account live_account picks, so it runs only with HANDLINE_LIVE=1, never from a suite.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import live_account
from live_e2e import MODEL_S, REPO, Answerer, Daemon, Refused, Stream, agent_changed, start_daemon, stop_daemon, yes

LEAD, A, B = "talk.lead", "talk.a", "talk.b"
SAY = re.compile(r"(^|[;&|(]\s*)hl say\b")   # an hl say run, not a command whose text names it (a task note saying "no hl say")
AGENTS = (LEAD, A, B)
EDGES = {(LEAD, A), (LEAD, B), (A, LEAD), (B, LEAD), (LEAD, "human")}
RUN_S = 480.0
GRACE_S = 45.0   # after the final: long enough to see a second report

ROLES = {
    "A": {LEAD: "You lead talk.a and talk.b. Give them work with `hl task new --assign <agent>` or talk with `hl say <agent> \"...\"`. "
                "Report to the founder with `hl say human \"...\"`.",
          "builder": "You work for talk.lead. Answer it with `hl say talk.lead \"...\"` or close a task you were given with "
                     "`hl task done T<n> \"<evidence>\"`."},
    "B": {LEAD: "You lead talk.a and talk.b. hl say is not available: give them work only with `hl task new --assign <agent>`; "
                "their answer is the task's evidence. Report to the founder by closing the founder's task with "
                "`hl task done T<n> \"<evidence>\"`.",
          "builder": "You work for talk.lead. hl say is not available: answer only by closing the task you were given with "
                     "`hl task done T<n> \"<evidence>\"`."},
    "N": {LEAD: "You lead talk.a and talk.b. hl say is not available: give them work only with `hl task new --assign <agent>`; "
                "their answer is the task's evidence. Report to the founder by writing the report as plain text in this "
                "session: the founder reads what you write here. Do not close the founder's task.",
          "builder": "You work for talk.lead. hl say is not available: answer only by closing the task you were given with "
                     "`hl task done T<n> \"<evidence>\"`."},
}


def refuse_say(prompt: dict) -> str:
    """Yes to every prompt but an hl say: the designs that have no hl say refuse it where the agent tries it."""
    if SAY.search(prompt.get("command") or ""):
        return next((o["key"] for o in prompt.get("options") or [] if o.get("answer") == "no"), "no")
    return yes(prompt)


def setup(root: Path, account: dict, roles: dict[str, str]) -> tuple[Path, str, dict[str, Path], dict[str, str]]:
    """The test daemon's home: founder key, the picked account, the shared settings, three structured haiku agents in git checkouts
    of their own (the builders' with a secret word), and the edges the job uses."""
    home, real = root / "state", Path(account["home"])
    store = root / ".config" / "orcapipe" / "protected" / "".join(c if c.isalnum() else "-" for c in str(home)) / "founder-keys"
    store.mkdir(parents=True, mode=0o700)
    key = secrets.token_urlsafe(32)
    (store / "key").write_text(key)
    (store / "key").chmod(0o600)
    acct = home / "accounts" / account["account"]
    acct.mkdir(parents=True)
    for f in ("status.json", "subscription.json"):
        if (real / "accounts" / account["account"] / f).exists():
            shutil.copy(real / "accounts" / account["account"] / f, acct / f)
    (home / "shared" / ".claude").mkdir(parents=True)
    shutil.copy(real / "shared" / ".claude" / "settings.json", home / "shared" / ".claude" / "settings.json")
    works, words = {}, {}
    for name in AGENTS:
        work = works[name] = root / "work" / name
        work.mkdir(parents=True)
        if name != LEAD:
            words[name] = secrets.choice(["amber", "basil", "cedar", "delta", "ember", "fjord", "garnet", "harbor"]) + str(secrets.randbelow(900) + 100)
            (work / "secret.txt").write_text(words[name] + "\n")
        git = ["git", "-c", "user.name=agent-talk", "-c", "user.email=agent-talk@localhost"]
        subprocess.run([*git, "init", "-q"], cwd=work, check=True)
        subprocess.run([*git, "add", "-A"], cwd=work, check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "agent talk"], cwd=work, check=True)
        d = home / "agents" / name
        d.mkdir(parents=True)
        (d / "agent.yaml").write_text(f"model: haiku\nmode: default\nstructured: true\nrepo: {work}\n")
        (d / "ROLE.md").write_text(f"# {name}\n{roles[LEAD if name == LEAD else 'builder']}\n")
    (home / "team.yaml").write_text(
        "agents:\n" + "".join(f"  {n}: {{}}\n" for n in AGENTS) + "edges:\n"
        '  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }\n'
        + "".join(f'  - {{ from: "{LEAD}", to: "{b}", relation: supervisor, response: async }}\n' for b in (A, B)))
    return home, key, works, words


def final_of(ev: dict, words: dict[str, str], design: str) -> str | None:
    """The channel this event reports to the founder on, if it does: a message to the founder (hl say, or a task handed to it) or the founder's task closed, with or
    without the words; in N, lead text with both words (any other lead text is not told from talking to itself)."""
    data = ev.get("data") or {}
    both = lambda text: bool(text) and all(w in text for w in words.values())
    if ev["type"] == "chat.message" and data.get("from") == LEAD and data.get("to") == "human":
        return "message to founder"
    if ev["type"] == "task.changed" and data.get("from") == "human" and data.get("state") in ("done", "reported"):
        return "task done"
    if design == "N" and ev["type"] == "transcript.event" and data.get("agent") == LEAD and data.get("kind") == "text" and both(data.get("text")):
        return "session text"
    return None


def count(events: list[tuple[float, dict]], words: dict[str, str], design: str, t0: float) -> dict:
    """The run's numbers from everything the stream saw after the founder's task."""
    msgs, tasks, texts, sends, say_tries, first = {}, {}, {}, [], [], None
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    cost = 0.0
    for at, ev in events:
        if at < t0:
            continue
        data = ev.get("data") or {}
        if first is None and final_of(ev, words, design):
            first = (at, final_of(ev, words, design))
        if ev["type"] == "chat.message":
            msgs[data["id"]] = data
        elif ev["type"] == "task.changed":
            tasks[data["id"]] = data
        elif ev["type"] == "transcript.event":
            if data.get("kind") == "text":
                texts[(data["agent"], data.get("seq"))] = (at, data)
            elif data.get("kind") == "tool_use" and "hl " in json.dumps((data.get("tool_use") or {}).get("input")):
                sends.append((at, data["agent"]))
                if SAY.search(str((data["tool_use"].get("input") or {}).get("command") or "")):
                    say_tries.append(data["agent"])
            elif data.get("kind") == "turn_end":
                end = data.get("turn_end") or {}
                cost += end.get("cost_usd") or 0
                for k in tokens:
                    tokens[k] += (end.get("usage") or {}).get(k) or 0
    both = lambda text: all(w in (text or "") for w in words.values())
    said = [m.get("body") for m in msgs.values() if m.get("from") == LEAD and m.get("to") == "human"]
    # A closed task's evidence reaches people in the task board's notice ("T1 reported done by talk.b: <evidence>"), not on the task.
    closed = [m.get("body") for m in msgs.values() if m.get("from") == "task-board" and m.get("to") == "human" and "done by" in (m.get("body") or "")]
    final_text = {k for k, (at, d) in texts.items() if design == "N" and d["agent"] == LEAD and both(d.get("text"))}
    finals = len(said) + len(closed) + len(final_text)
    reported = said + closed + [texts[k][1].get("text") for k in final_text]
    final_ok = any(both(x) for x in said + closed) or bool(final_text)
    # Sends an agent made: the daemon's own notices (from task-board) and the founder's task are not counted.
    pairs = [(m.get("from"), m.get("to")) for m in msgs.values()] + [(t.get("from"), t.get("to")) for t in tasks.values()]
    wrong = sum(1 for p in pairs if p[0] in AGENTS and p not in EDGES) + (len(say_tries) if design != "A" else 0)
    to_lead = {}
    for b, w in words.items():
        n = sum(1 for m in msgs.values() if m.get("from") in (b, "task-board") and m.get("to") == LEAD and w in (m.get("body") or ""))
        to_lead[b] = n
    stray = [v for k, v in texts.items() if k not in final_text]
    names = {LEAD: (A, B, "talk.a", "talk.b"), A: (LEAD,), B: (LEAD,)}
    echo = sum(1 for at, d in stray
               if any(a == d["agent"] and 0 <= at - sat <= 60 for sat, a in sends)
               and (any(n in d.get("text", "") for n in names[d["agent"]]) or any(w in d.get("text", "") for w in words.values())))
    return {
        "finished": round(first[0] - t0, 1) if first else None,
        "final_via": first[1] if first else None,
        "wrong_sends": wrong,
        "say_tries": len(say_tries),
        "double_reports": sum(max(0, n - 1) for n in to_lead.values()) + max(0, finals - 1),
        "final_has_words": final_ok,
        "lost": sum(1 for w in words.values() if not any(w in (x or "") for x in reported)),
        "stray_text": len(stray),
        "stray_echo": echo,
        "stray_samples": [f"{d['agent']}: {d.get('text', '')[:120]}" for at, d in stray[:4]],
        "messages": len(msgs),
        "tasks": len(tasks),
        "tokens": tokens,
        "cost_usd": round(cost, 4),
    }


def one_run(binary: str, account: dict, design: str, keep: bool) -> dict:
    root = Path(tempfile.mkdtemp(prefix="agent-talk.")).resolve()
    group = None
    try:
        home, key, works, words = setup(root, account, ROLES[design])
        group = start_daemon(binary, root, home, account, agents=works)
        sock = home / "daemon.sock"
        d = Daemon(sock, key)
        s = Stream(sock, key)
        Stream(sock, key, ["transcript"], into=s)
        for name in AGENTS:
            Answerer(sock, key, s, name, refuse_say if design != "A" else yes)
        t = time.time()
        for name in AGENTS:
            d.write("session.start", {"agent": name})
        for name in AGENTS:
            if not s.wait(t, agent_changed(name, "idle"), t + MODEL_S):
                raise Refused(f"{name} did not come up; daemon log {root / 'daemon.log'}")
        t0 = time.time()
        d.write("task.new", {"owner": LEAD, "title": "Collect the builders' secret words",
                             "body": f"Get the secret word talk.a keeps in secret.txt in its own checkout, and the one talk.b keeps in "
                                     f"its own, and report both words to the founder. Only each builder can read its own file."})
        got = s.wait(t0, lambda ev: final_of(ev, words, design) is not None, t0 + RUN_S)
        if got:
            s.wait(got[0], lambda ev: False, time.time() + GRACE_S)
        with s.cond:
            events = sorted(s.events, key=lambda e: e[0])
        if keep:
            (root / "events.json").write_text(json.dumps(events))
        return {"design": design, **count(events, words, design, t0)}
    finally:
        if group:
            stop_daemon(group)
        if keep:
            print(f"agent-talk: kept {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--design", choices=sorted(ROLES), required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep each run's test root (home, daemon log)")
    args = ap.parse_args()
    if os.environ.get("HANDLINE_LIVE") != "1":
        print("agent-talk: spends real tokens; HANDLINE_LIVE=1 runs it", file=sys.stderr)
        return 2
    account = live_account.pick_live()
    print(f"agent-talk: account {account['account']}: {account['why']}", flush=True)
    build = Path(tempfile.mkdtemp(prefix="agent-talk-bin."))
    binary = str(build / "handlined")
    subprocess.run(["go", "build", "-o", binary, "./cmd/handlined"], cwd=REPO / "go", check=True)
    rows = []
    try:
        for i in range(args.runs):
            try:
                row = one_run(binary, account, args.design, args.keep)
            except (Refused, OSError, subprocess.CalledProcessError) as e:
                row = {"design": args.design, "error": str(e)}
            rows.append(row)
            print(f"agent-talk: {args.design} run {i + 1}: {json.dumps(row)}", flush=True)
    except KeyboardInterrupt:
        print("agent-talk: interrupted", file=sys.stderr)
    finally:
        shutil.rmtree(build, ignore_errors=True)
    if args.json:
        print(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
