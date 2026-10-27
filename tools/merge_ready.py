"""Merges a pull request only when it is ready: tools/merge-ready.sh PR runs this.

Refuses unless: the PR is open, on main, not a draft; every check main requires is green on its head; GitHub finds no conflict; the PR's audit
task (the highest-numbered task titled "Audit PR <n> ...") is done with evidence "PASS ... @<sha>" for the head sha; and for a PR that touches
an app (apps/, packages/app-*, packages/tokens) its ux task (title with "ux" and "PR <n>") is done with "PASS ... live ... @<sha>".
Otherwise runs `gh pr merge --merge` and says what it did. It reads through gh and hl and has no way to be told to skip a check.
"""
import json
import re
import subprocess
import sys

UI_PATH = re.compile(r"^(apps/|packages/app-|packages/tokens/)")
TASK_LINE = re.compile(r"^T(\d+)\s+(.*?)\s+\[[^\]]*\]\s+(\w+)\b")


def run(*cmd: str) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def tasks_for(listing: str, pr: int, kind: str) -> list[tuple[int, str, str]]:
    """The (id, title, state) of the tasks in an `hl task list` listing that are the PR's audit ('audit') or ux ('ux') task, by id."""
    n = rf"PR\s*#?{pr}(?!\d)"
    title = {"audit": re.compile(rf"^audit\b.*{n}", re.I), "ux": re.compile(rf"(\bux\b.*{n}|{n}.*\bux\b)", re.I)}[kind]
    out = []
    for line in listing.splitlines():
        m = TASK_LINE.match(line)
        if m and title.search(m.group(2)):
            out.append((int(m.group(1)), m.group(2), m.group(3)))
    return sorted(out)


def evidence_of(show: str) -> str:
    m = re.search(r"^done \S+ \S+: (.*)$", show, re.M)
    return m.group(1).strip() if m else ""


def verdict(evidence: str, head: str, live: bool) -> str:
    """'' when the evidence is a PASS for head (and says it was live, if asked), else why not."""
    if not re.match(r"PASS\b", evidence):
        return "its evidence does not start with PASS"
    shas = re.findall(r"@([0-9a-f]{7,40})\b", evidence, re.I)
    if not shas:
        return "its evidence names no head sha (@<sha>)"
    if not any(head.lower().startswith(s.lower()) for s in shas):
        return f"its evidence is for {shas[0]}, not the head {head[:9]}"
    if live and not re.search(r"\blive\b", evidence, re.I):
        return "its evidence does not say it was checked live"
    return ""


def problems(pr: dict, required: list[str], listing: str, show) -> list[str]:
    """Why the PR may not be merged; empty when it may. show(id) is `hl task show` of one task."""
    n, head = pr["number"], pr["headRefOid"]
    out = []
    if pr["state"] != "OPEN":
        out.append(f"PR {n} is {pr['state'].lower()}")
    if pr["baseRefName"] != "main":
        out.append(f"PR {n} is based on {pr['baseRefName']}, not main")
    if pr["isDraft"]:
        out.append(f"PR {n} is a draft")
    seen = {c.get("name"): c for c in pr["statusCheckRollup"]}
    for name in required:
        c = seen.get(name)
        if c is None:
            out.append(f"required check {name} has not run on the head")
        elif c.get("status") != "COMPLETED":
            out.append(f"required check {name} is still running")
        elif c.get("conclusion") != "SUCCESS":
            out.append(f"required check {name} is {str(c.get('conclusion')).lower()}")
    if pr["mergeable"] == "CONFLICTING":
        out.append("the PR has conflicts with main")
    elif pr["mergeable"] != "MERGEABLE":
        out.append("GitHub has not worked out whether it merges cleanly yet; try again in a minute")
    kinds = [("audit", False)]
    if any(UI_PATH.match(f["path"]) for f in pr["files"]):
        kinds.append(("ux", True))
    for kind, live in kinds:
        found = tasks_for(listing, n, kind)
        if not found:
            out.append(f"no {kind} task for PR {n} (a task titled '{'Audit' if kind == 'audit' else 'UX'} PR {n} ...')")
            continue
        tid, _, state = found[-1]
        if state != "done":
            out.append(f"the {kind} task T{tid} is {state}, not done")
            continue
        why = verdict(evidence_of(show(tid)), head, live)
        if why:
            out.append(f"the {kind} task T{tid}: {why}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].isdigit():
        print("usage: tools/merge-ready.sh <PR number>", file=sys.stderr)
        return 2
    n = int(argv[1])
    try:
        pr = json.loads(run("gh", "pr", "view", str(n), "--json",
                            "number,state,isDraft,baseRefName,headRefOid,mergeable,statusCheckRollup,files"))
        required = json.loads(run("gh", "api", "repos/{owner}/{repo}/branches/main/protection/required_status_checks", "--jq", ".contexts"))
        listing = run("hl", "task", "list", "--everyone", "--all")
        bad = problems(pr, required, listing, lambda tid: run("hl", "task", "show", f"T{tid}"))
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"merge-ready: cannot tell whether PR {n} is ready, so it is not merged: {e}", file=sys.stderr)
        return 1
    if bad:
        print(f"merge-ready: PR {n} is NOT merged:", file=sys.stderr)
        for b in bad:
            print(f"  - {b}", file=sys.stderr)
        return 1
    try:
        run("gh", "pr", "merge", str(n), "--merge", "--match-head-commit", pr["headRefOid"])
    except RuntimeError as e:
        print(f"merge-ready: gh refused: {e}", file=sys.stderr)
        return 1
    print(f"merge-ready: merged PR {n} at {pr['headRefOid'][:9]} (checks {', '.join(required)} green, no conflicts, audit PASS for this head"
          f"{', ux live PASS' if any(UI_PATH.match(f['path']) for f in pr['files']) else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
