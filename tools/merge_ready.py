"""Merges a pull request only when it is ready: tools/merge-ready.sh PR runs this.

Refuses unless: the PR is open, on main, not a draft; every check main requires is green on its head; GitHub finds no conflict; the PR's audit
task (the highest-numbered task titled "Audit PR <n> ..." or "Re-audit PR <n> ...", by the first PR number in the title) is done with evidence
"PASS ... @<head>" or "PR #<n> @<head> PASS"; and for a PR that touches an app (apps/, packages/app-*, packages/tokens) its ux task (assigned
to handline.ux, or titled "Live check PR <n>" / "Re-check PR <n>") is done with evidence "UX live PASS [on #<n>] @<head>" or "Web half PASS ...". A PR of 100 or more files, and branch
protection with no required checks, are refused.
Otherwise runs `gh pr merge --merge` and says what it did. It reads through gh and hl and has no way to be told to skip a check.
"""
import json
import re
import subprocess
import sys

UI_PATH = re.compile(r"^(apps/|packages/app-|packages/tokens/)")
TASK_LINE = re.compile(r"^T(\d+)\s+(.*?)\s+\[([^\]]*)\]\s+(\w+)\b")


def run(*cmd: str) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def first_pr(title: str) -> int:
    m = re.search(r"PR\s*#?(\d+)", title, re.I)
    return int(m.group(1)) if m else 0


def tasks_for(listing: str, pr: int, kind: str) -> list[tuple[int, str, str]]:
    """The (id, title, state) of the tasks in an `hl task list` listing that are the PR's audit ('audit': titled Audit or Re-audit) or ux ('ux':
    assigned to handline.ux, or titled Live check or Re-check) task, by id. A title names the PR of its first 'PR <n>' only."""
    title = {"audit": re.compile(r"^(re-?)?audit\b", re.I), "ux": re.compile(r"^(live check|re-?check)\b", re.I)}[kind]
    out = []
    for line in listing.splitlines():
        m = TASK_LINE.match(line)
        if not m or first_pr(m.group(2)) != pr:
            continue
        if title.search(m.group(2)) or (kind == "ux" and m.group(3) == "handline.ux"):
            out.append((int(m.group(1)), m.group(2), m.group(4)))
    return sorted(out)


def evidence_of(show: str) -> str:
    m = re.search(r"^done \S+ \S+: (.*)$", show, re.M)
    return m.group(1).strip() if m else ""


SHA = r"@([0-9a-f]{7,40})\b"
# An audit's evidence says PASS first and the head right after ("PASS #7 @sha", "PASS: PR #7 @sha"), or PR and head first and PASS right after
# ("PR #7 @sha PASS"). Nothing else may stand before the verdict, so "Findings, no PASS @sha" and "NOT PASS @sha" are not a pass.
AUDIT_PASS = [re.compile(rf"PASS\b[:,]?\s*(?:on\s+)?(?:PR\s*)?(?:#(\d+)\s*)?{SHA}"), re.compile(rf"(?:PR\s*#?(\d+)\s+)?{SHA}\s+PASS\b")]
# A ux pass is "UX live PASS" or "Web half PASS", optionally "on #<n>" (the PR), then the head.
UX_PASS = re.compile(rf"(?:UX live|Web half) PASS(?:\s+on\s+(?:PR\s*)?#(\d+))?\s+{SHA}")


def verdict(evidence: str, head: str, pr: int, live: bool) -> str:
    """'' when the evidence is a PASS for head (and for PR pr, if it names one), else why not."""
    if live:
        m = UX_PASS.match(evidence)
        form = 'its evidence does not start with "UX live PASS ... @<sha>"'
        n, sha = (m.group(1), m.group(2)) if m else (None, None)
    else:
        form = "its evidence does not say PASS first with the head right after, or PR and head first and then PASS"
        m = next((x for x in (r.match(evidence) for r in AUDIT_PASS) if x), None)
        n, sha = (m.group(1), m.group(2)) if m else (None, None)
    if not m:
        return form
    if n is not None and int(n) != pr:
        return f"its evidence is for PR {n}, not {pr}"
    if not head.lower().startswith(sha.lower()):
        return f"its evidence is for {sha}, not the head {head[:9]}"
    return ""


def newest_checks(rollup: list[dict]) -> dict[str, dict]:
    """The rollup by check name, the newest run of a name that ran more than once (one still running counts as the newest)."""
    out: dict[str, dict] = {}
    for c in rollup:
        name = c.get("name") or c.get("context")
        if "conclusion" not in c and "state" in c:  # a commit status, not a check run
            c = {**c, "status": "COMPLETED", "conclusion": c["state"]}
        at = c.get("completedAt") or "9999" if c.get("status") != "COMPLETED" else c.get("completedAt") or ""
        if name not in out or at >= out[name]["_at"]:
            out[name] = {**c, "_at": at}
    return out


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
    if not required:
        out.append("branch protection lists no required checks, so nothing says the PR is green")
    if len(pr["files"]) >= 100:
        out.append("the PR lists 100 or more files, so whether it touches an app cannot be told")
    seen = newest_checks(pr["statusCheckRollup"])
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
        why = verdict(evidence_of(show(tid)), head, n, live)
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
