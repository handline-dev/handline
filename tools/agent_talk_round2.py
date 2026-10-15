#!/usr/bin/env python3
"""Round 2 of the agent-talk experiment: the founder talks with an agent in its own session, and agents tell the founder
nothing by repeating what they sent each other there. Same toy job, test daemon and haiku agents as tools/agent_talk.py.

  HANDLINE_LIVE=1 python3 tools/agent_talk_round2.py --runs 6 [--conditions none rule noop md] --keep DIR
  python3 tools/agent_talk_round2.py --recount DIR      # the rows again, from the kept runs

Channels, in every condition: an agent talks to the founder by writing in its own session (every text block is taken as
delivered, except one that is exactly `noop`); `hl say human` answers "use the chat instead" (a shim in front of hl: the
daemon has no such reply); agents talk to each other with `hl say <agent>` and tasks. Conditions, added to every ROLE.md:
- none: nothing (round 1's baseline);
- rule: "After you send to another agent, do not repeat it in your session; session text is only for the founder.";
- noop: "When you have nothing to tell the founder, end your turn with exactly `noop`.";
- md: as noop, and every hl say body goes through `--file -` in Markdown.
Every second run the founder asks the lead mid-job, in its session, to reply with a code, so a reply to the founder is tested.

Each run counts, from the event stream after the founder's task (count() is the definition):
- report: any agent's delivered text, or the founder's task closed, with both words; success = one reached the founder;
- missed reports: no report, and a founder question whose code no delivered lead text carries;
- echoes: delivered text other than a report or a reply to the founder, written within a minute of the same agent's hl
  command and naming another agent or a word (round 1's definition);
- half-kept: delivered text that says noop but is not exactly noop ("noop. sent to talk.a");
- wordless closes: a builder's `hl task done T<n>` that does not carry its word;
- tokens and cost from each turn_end, and the time from the founder's task to the report.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import agent_talk as r1
import live_account
from live_e2e import MODEL_S, REPO, Answerer, Daemon, Refused, Stream, agent_changed, start_daemon, stop_daemon, yes

LEAD, A, B, AGENTS = r1.LEAD, r1.A, r1.B, r1.AGENTS
RUN_S, GRACE_S, ASK_S = 480.0, 45.0, 120.0
MIN_FREE = 5 << 30

CHANNEL = "The founder reads everything you write in this session; that is how you talk to the founder."
ROLES = {LEAD: "You lead talk.a and talk.b. Give them work with `hl task new --assign <agent>` or talk with "
               "`hl say <agent> \"...\"`. " + CHANNEL,
         "builder": "You work for talk.lead. Answer it with `hl say talk.lead \"...\"` or close a task you were given with "
                    "`hl task done T<n> \"<evidence>\"`. " + CHANNEL}
NOOP = "When you have nothing to tell the founder, end your turn with exactly `noop`."
CONDITIONS = {
    "none": "",
    "rule": "After you send to another agent, do not repeat it in your session; session text is only for the founder.",
    "noop": NOOP,
    "md": NOOP + " Write every hl say body in Markdown and send it through `--file -`, for example "
                 "`hl say talk.lead --file - <<'EOF'` then the body, then `EOF`.",
}
# hl say to the founder answers as the native design would; everything else is the real hl.
SHIM = """#!/bin/sh
if [ "$1" = say ]; then
  t="" skip=1
  for a in "$@"; do
    if [ "$skip" = 1 ]; then skip=0; continue; fi
    case "$a" in --file|--parent|--task|--no-task) skip=1 ;; -*) ;; *) t="$a"; break ;; esac
  done
  case "$t" in human|founder) echo "hl say $t: use the chat instead. The founder reads what you write in this session." >&2; exit 1 ;; esac
fi
exec %s "$@"
"""
HL = re.compile(r"(^|[;&|(]\s*)hl\s")
SAY_FOUNDER = re.compile(r"(^|[;&|(]\s*)hl say\b(\s+-\S+(\s+\S+)?)*\s+(human|founder)\b")
NOOP_WORD = re.compile(r"\bnoop\b", re.I)
MD = re.compile(r"^\s*(#{1,6} |[-*] |\d+\. |\|)|\*\*|`", re.M)


def delivered(text: str) -> bool:
    return text.strip() != "noop"


def count(events: list[tuple[float, dict]], meta: dict) -> dict:
    """The run's numbers from everything the stream saw after the founder's task."""
    words, ack, t0, ask_at = meta["words"], meta.get("ack"), meta["t0"], meta.get("ask_at")
    both = lambda text: all(w in (text or "") for w in words.values())
    msgs, texts, sends, say_founder, wordless, started, last, ended = {}, [], [], 0, 0, {}, {}, set()
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    cost = 0.0
    for at, ev in events:
        if at < t0:
            continue
        data = ev.get("data") or {}
        if ev["type"] == "chat.message":
            msgs[data["id"]] = (at, data)
        elif ev["type"] == "transcript.event":
            if data.get("kind") == "turn_start":
                started[(data["agent"], data.get("session"), data.get("turn"))] = (data.get("turn_start") or {}).get("from")
            elif data.get("kind") == "text":
                last[(data["agent"], data.get("session"), data.get("turn"))] = data.get("text") or ""
                texts.append((at, data["agent"], data.get("text") or "", started.get((data["agent"], data.get("session"), data.get("turn")))))
            elif data.get("kind") == "tool_use":
                cmd = str(((data.get("tool_use") or {}).get("input") or {}).get("command") or "")
                if HL.search(cmd):
                    sends.append((at, data["agent"]))
                if SAY_FOUNDER.search(cmd):
                    say_founder += 1
                if data["agent"] in words and re.search(r"hl task done T\d+", cmd) and words[data["agent"]] not in cmd:
                    wordless += 1
            elif data.get("kind") == "turn_end":
                ended.add((data["agent"], data.get("session"), data.get("turn")))
                end = data.get("turn_end") or {}
                cost += end.get("cost_usd") or 0
                for k in tokens:
                    tokens[k] += (end.get("usage") or {}).get(k) or 0
    # Under "deliver only a turn the founder started", what would the founder still see: those turns' text (the job's own
    # first turn is the founder's task, so it counts too), and the reply to the question.
    founder_turns = [(at, a, t) for at, a, t, by in texts if by == "human" and delivered(t)]
    texts = [(at, a, t) for at, a, t, by in texts]
    out = [(at, a, t) for at, a, t in texts if delivered(t)]
    reports = [(at, f"session text ({a})") for at, a, t in out if both(t)]
    # A closed task's evidence reaches the founder in the task board's notice ("T1 reported done by talk.lead: <evidence>").
    reports += [(at, "task done") for at, m in msgs.values()
                if m.get("from") == "task-board" and m.get("to") == "human" and "done by" in (m.get("body") or "") and both(m.get("body"))]
    reports.sort()
    replied = bool(ack) and any(a == LEAD and ack in t and at >= ask_at for at, a, t in out)
    reply = lambda a, t: bool(ack) and a == LEAD and ack in t
    stray = [(at, a, t) for at, a, t in out if not both(t) and not reply(a, t)]
    names = {LEAD: (A, B), A: (LEAD,), B: (LEAD,)}
    echo = [(at, a, t) for at, a, t in stray if any(sa == a and 0 <= at - sat <= 60 for sat, sa in sends)
            and (any(n in t for n in names[a]) or any(w in t for w in words.values()))]
    between = [m for at, m in msgs.values() if m.get("from") in AGENTS and m.get("to") in AGENTS]
    return {
        "condition": meta["condition"],
        "models": sorted({m for ms in (meta.get("models") or {}).values() for m in ms}),
        "asked": bool(ack),
        "success": bool(reports),
        "finished": round(reports[0][0] - t0, 1) if reports else None,
        "report_via": reports[0][1] if reports else None,
        "double_reports": max(0, len(reports) - 1),
        "missed_reports": (0 if reports else 1) + (1 if ack and not replied else 0),
        "replied_to_founder": replied if ack else None,
        "replied_in_founder_turn": any(ack in t and at >= ask_at for at, a, t in founder_turns) if ack else None,
        "founder_turn_texts": len(founder_turns),
        "founder_turn_reports": sum(1 for at, a, t in founder_turns if both(t)),
        "success_founder_turns_only": any(both(t) for at, a, t in founder_turns) or any(v == "task done" for at, v in reports),
        "echoes": len(echo),
        "stray_delivered": len(stray),
        "half_kept": sum(1 for at, a, t in out if NOOP_WORD.search(t)),
        "noop_turns": sum(1 for at, a, t in texts if not delivered(t)),
        # How each turn of the job (started after the founder's task, and ended) ended: its last text block, or none.
        "turns_ended": len(ended & started.keys()),
        "turns_ended_in_text": sum(1 for k in ended & started.keys() if k in last and delivered(last[k])),
        "say_founder_tries": say_founder,
        "wordless_done": wordless,
        "says_between_agents": len(between),
        "says_with_markdown": sum(1 for m in between if MD.search(m.get("body") or "")),
        "echo_samples": [f"{a}: {t[:120]}" for at, a, t in echo[:4]],
        "tokens": tokens,
        "cost_usd": round(cost, 4),
    }


def one_run(binary: str, account: dict, condition: str, ask: bool, keep: Path) -> dict:
    root = Path(tempfile.mkdtemp(prefix="agent-talk2.")).resolve()
    group = None
    try:
        extra = CONDITIONS[condition]
        roles = {k: f"{v} {extra}".strip() for k, v in ROLES.items()}
        home, key, works, words = r1.setup(root, account, roles)
        # A structured session gets neither agent.yaml's model nor ROLE.md from the daemon (no --model, no system prompt), so each
        # checkout carries them: the role as the project's CLAUDE.md, the model in its project settings.
        for name, work in works.items():
            (work / "CLAUDE.md").write_text(f"# {name}\n{roles[LEAD if name == LEAD else 'builder']}\n")
            (work / ".claude").mkdir()
            (work / ".claude" / "settings.json").write_text(json.dumps({"model": "haiku"}))
        shim = root / "shim"
        shim.mkdir()
        (shim / "hl").write_text(SHIM % shutil.which("hl"))
        (shim / "hl").chmod(0o755)
        group = start_daemon(binary, root, home, account, agents=works, env_extra={"PATH": f"{shim}:{os.environ['PATH']}"})
        sock = home / "daemon.sock"
        d, s = Daemon(sock, key), Stream(sock, key)
        Stream(sock, key, ["transcript"], into=s)
        for name in AGENTS:
            Answerer(sock, key, s, name, yes)
        t = time.time()
        for name in AGENTS:
            d.write("session.start", {"agent": name})
        for name in AGENTS:
            if not s.wait(t, agent_changed(name, "idle"), t + MODEL_S):
                raise Refused(f"{name} did not come up; daemon log {root / 'daemon.log'}")
        meta = {"condition": condition, "words": words, "t0": time.time(), "ack": None, "ask_at": None}
        d.write("task.new", {"owner": LEAD, "title": "Collect the builders' secret words",
                             "body": "Get the secret word talk.a keeps in secret.txt in its own checkout, and the one talk.b keeps in "
                                     "its own, and report both words to the founder. Only each builder can read its own file."})
        t0 = meta["t0"]
        both = lambda ev: ev["type"] == "transcript.event" and (ev.get("data") or {}).get("kind") == "text" \
            and delivered((ev["data"].get("text") or "")) and all(w in ev["data"].get("text", "") for w in words.values())
        closed = lambda ev: ev["type"] == "chat.message" and (ev.get("data") or {}).get("from") == "task-board" \
            and "done by" in (ev["data"].get("body") or "") and all(w in ev["data"]["body"] for w in words.values())
        reported = lambda ev: both(ev) or closed(ev)
        if ask:   # once the lead has started the job, the founder asks in its session
            hl_by_lead = lambda ev: (ev.get("data") or {}).get("agent") == LEAD and (ev.get("data") or {}).get("kind") == "tool_use" \
                and HL.search(str(((ev["data"].get("tool_use") or {}).get("input") or {}).get("command") or ""))
            if s.wait(t0, lambda ev: hl_by_lead(ev) or reported(ev), t0 + RUN_S):
                meta["ack"], meta["ask_at"] = f"ACK{secrets.randbelow(900) + 100}", time.time()
                d.write("chat.send", {"agent": LEAD, "body": f"Founder here, a quick check while you work: reply to me with the code "
                                                             f"{meta['ack']} so I know you see my messages, then carry on."})
        got = s.wait(t0, reported, t0 + RUN_S)
        if meta["ack"]:
            s.wait(meta["ask_at"], lambda ev: (ev.get("data") or {}).get("agent") == LEAD and meta["ack"] in str((ev["data"]).get("text") or ""),
                   meta["ask_at"] + ASK_S)
        if got:
            s.wait(time.time(), lambda ev: False, time.time() + GRACE_S)
        with s.cond:
            events = sorted(s.events, key=lambda e: e[0])
        meta["models"] = models_of(account, works)
        keep.mkdir(parents=True)
        (keep / "events.json").write_text(json.dumps(events))
        (keep / "meta.json").write_text(json.dumps(meta))
        return count(events, meta)
    finally:
        if group:
            stop_daemon(group)
        shutil.rmtree(root, ignore_errors=True)


def models_of(account: dict, works: dict[str, Path]) -> dict[str, list[str]]:
    """The models that answered in each agent's transcript (its assistant messages): the CLI names a project folder after its cwd."""
    projects = Path(account["home"]) / "accounts" / account["account"] / ".claude" / "projects"
    out = {}
    for name, work in works.items():
        seen = set()
        for f in (projects / re.sub(r"[^A-Za-z0-9]", "-", str(work))).glob("*.jsonl"):
            for line in f.read_text(errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") == "assistant" and (rec.get("message") or {}).get("model"):
                    seen.add(rec["message"]["model"])
        out[name] = sorted(seen)
    return out


def recount(dirs: list[Path]) -> list[dict]:
    return [{"run": p.name, **count(json.loads((p / "events.json").read_text()), json.loads((p / "meta.json").read_text()))}
            for p in sorted(dirs) if (p / "meta.json").exists()]


def table(rows: list[dict]) -> str:
    """The report's table: one column per condition, medians with min-max, and totals."""
    cols = [c for c in CONDITIONS if any(r.get("condition") == c and "error" not in r for r in rows)]
    by = {c: [r for r in rows if r.get("condition") == c and "error" not in r] for c in cols}
    span = lambda xs, f="{:g}": f"{f.format(statistics.median(xs))} ({f.format(min(xs))}-{f.format(max(xs))})" if xs else "-"
    total = lambda rs, k: sum(r[k] for r in rs)
    via = lambda rs: ", ".join(f"{n}x {v}" for v, n in sorted(Counter((r["report_via"] or "none").split(" (")[0] for r in rs).items()))
    lines = {
        "Runs (founder asked mid-job)": lambda rs: f"{len(rs)} ({sum(r['asked'] for r in rs)})",
        "Report with both words reached the founder": lambda rs: f"{sum(r['success'] for r in rs)}/{len(rs)}",
        "First report's channel": via,
        "Time to the report, s, median (min-max)": lambda rs: span([r["finished"] for r in rs if r["finished"] is not None], "{:.0f}"),
        "Missed reports": lambda rs: str(total(rs, "missed_reports")),
        "Founder's question answered": lambda rs: f"{sum(bool(r['replied_to_founder']) for r in rs if r['asked'])}/{sum(r['asked'] for r in rs)}",
        "... in the turn the question started": lambda rs: f"{sum(bool(r['replied_in_founder_turn']) for r in rs if r['asked'])}/{sum(r['asked'] for r in rs)}",
        "Text in founder-started turns (of it, reports)": lambda rs: f"{total(rs, 'founder_turn_texts')} ({total(rs, 'founder_turn_reports')})",
        "Report still reached the founder with only those turns and task done": lambda rs: f"{sum(r['success_founder_turns_only'] for r in rs)}/{len(rs)}",
        "Echoes reaching the founder, per run (total)": lambda rs: f"{span([r['echoes'] for r in rs])} ({total(rs, 'echoes')})",
        "Other stray text reaching the founder (total)": lambda rs: str(total(rs, "stray_delivered") - total(rs, "echoes")),
        "Half-kept noop": lambda rs: str(total(rs, "half_kept")),
        "Turns ended with exactly noop": lambda rs: str(total(rs, "noop_turns")),
        "Ended turns whose last block is text": lambda rs: f"{total(rs, 'turns_ended_in_text')}/{total(rs, 'turns_ended')}",
        "Double reports": lambda rs: str(total(rs, "double_reports")),
        "Builder closed its task without its word (e.g. \"request #2\")": lambda rs: str(total(rs, "wordless_done")),
        "hl say to the founder tried (refused)": lambda rs: str(total(rs, "say_founder_tries")),
        "Cost per run, $, median (min-max)": lambda rs: span([r["cost_usd"] for r in rs], "{:.3f}"),
        "Output tokens per run, median (min-max)": lambda rs: span([r["tokens"]["output"] for r in rs], "{:.0f}"),
        "Messages between agents (in Markdown)": lambda rs: f"{total(rs, 'says_between_agents')} ({total(rs, 'says_with_markdown')})",
    }
    out = ["| | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    out += [f"| {name} | " + " | ".join(f(by[c]) for c in cols) + " |" for name, f in lines.items()]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--conditions", nargs="*", choices=list(CONDITIONS), default=["none", "rule", "noop"])
    ap.add_argument("--runs", type=int, default=6, help="runs per condition, interleaved; every second one asks the lead mid-job")
    ap.add_argument("--keep", type=Path, help="each run's events and meta go under DIR/<condition>-<n>")
    ap.add_argument("--recount", type=Path, help="print the rows and table again from a --keep DIR")
    args = ap.parse_args()
    if args.recount:
        rows = recount([p for p in args.recount.iterdir() if p.is_dir()])
        for row in rows:
            print(json.dumps(row))
        print(table(rows))
        return 0
    if os.environ.get("HANDLINE_LIVE") != "1" or not args.keep:
        print("agent-talk: spends real tokens; HANDLINE_LIVE=1 and --keep DIR run it", file=sys.stderr)
        return 2
    account = live_account.pick_live()
    print(f"agent-talk: account {account['account']}: {account['why']}", flush=True)
    build = Path(tempfile.mkdtemp(prefix="agent-talk-bin."))
    binary = str(build / "handlined")
    subprocess.run(["go", "build", "-o", binary, "./cmd/handlined"], cwd=REPO / "go", check=True)
    rows = []
    try:
        for i in range(args.runs):
            for c in args.conditions:
                if shutil.disk_usage(tempfile.gettempdir()).free < MIN_FREE:
                    print("agent-talk: under 5 GiB free on disk, stopping", file=sys.stderr)
                    raise KeyboardInterrupt
                keep = args.keep / f"{c}-{i + 1}"
                try:
                    row = one_run(binary, account, c, ask=i % 2 == 1, keep=keep)
                except (Refused, OSError, subprocess.CalledProcessError) as e:
                    row = {"condition": c, "error": str(e)}
                rows.append({"run": keep.name, **row})
                print(f"agent-talk: {keep.name}: {json.dumps(rows[-1])}", flush=True)
    except KeyboardInterrupt:
        print("agent-talk: interrupted", file=sys.stderr)
    finally:
        shutil.rmtree(build, ignore_errors=True)
    print(table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
