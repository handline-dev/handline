"""Bundles the ready pull requests into one: tools/bundle.sh [--without N ...] [--push].

Ready is tools/merge_ready.py's test. With 3 or more ready, builds bundle-<numbers> from main with a --no-ff merge of each head; a head that
moved since the check or conflicts is left out (under 2 left builds nothing). --push opens the bundle PR through tools/pr-open.sh. It never
merges. If the bundle's CI is red, rebuild with --without <the part that broke it>.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merge_ready  # noqa: E402
from merge_ready import run  # noqa: E402

MIN_PARTS = 3


def git(*args: str, cwd: str = ".") -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def abandon(start: str, detached: bool, cwd: str = ".") -> None:
    """Goes back to where the run began (a branch, or a detached sha) and removes bundle-wip."""
    back = git("switch", "-q", *(["--detach"] if detached else []), start, cwd=cwd)
    if back.returncode:
        raise RuntimeError(f"cannot go back to {start}: {back.stderr.strip()}")
    git("branch", "-D", "bundle-wip", cwd=cwd)


def ready_prs(without: set[int]) -> tuple[dict[int, str], dict[int, str]]:
    """({PR: head} of the open PRs on main that may be merged, {PR: first reason} of those that may not)."""
    required = merge_ready.required_checks()
    listing = run("hl", "task", "list", "--everyone", "--all")
    open_prs = json.loads(run("gh", "pr", "list", "--base", "main", "--state", "open", "--limit", "100", "--json", "number"))
    ready, skipped = {}, {}
    for n in sorted(p["number"] for p in open_prs if p["number"] not in without):
        pr = json.loads(run("gh", "pr", "view", str(n), "--json", merge_ready.PR_FIELDS + ",headRefName"))
        if pr["headRefName"].startswith("bundle-"):
            continue
        bad = merge_ready.problems(pr, required, listing, lambda tid: run("hl", "task", "show", f"T{tid}"))
        if bad:
            skipped[n] = bad[0]
        else:
            ready[n] = pr["headRefOid"]
    return ready, skipped


def fetch_heads(parts: dict[int, str], cwd: str = ".") -> tuple[dict[int, str], dict[int, str]]:
    """Fetches each PR head; (the parts whose head is still the checked one, {PR: why not} of the others)."""
    kept, dropped = {}, {}
    for n, sha in parts.items():
        got = git("fetch", "-q", "origin", f"pull/{n}/head", cwd=cwd)
        now = git("rev-parse", "FETCH_HEAD", cwd=cwd).stdout.strip() if got.returncode == 0 else ""
        if now == sha:
            kept[n] = sha
        else:
            dropped[n] = "its head moved since it was checked" if now else "its head could not be fetched"
    return kept, dropped


def merge_parts(parts: dict[int, str], cwd: str = ".") -> tuple[list[int], dict[int, str]]:
    """Merges each head (--no-ff, by PR number) into the current branch; a head that conflicts is left out."""
    merged, dropped = [], {}
    for n, sha in sorted(parts.items()):
        r = git("merge", "--no-ff", "-m", f"Merge PR #{n} ({sha[:9]}) into the bundle", sha, cwd=cwd)
        if r.returncode and git("diff", "--name-only", "--diff-filter=U", cwd=cwd).stdout.strip():
            git("merge", "--abort", cwd=cwd)
            dropped[n] = "it conflicts with main or an earlier part"
        elif r.returncode:
            raise RuntimeError(f"git merge of #{n} failed, not for a conflict: {(r.stderr or r.stdout).strip()}")
        else:
            merged.append(n)
    return merged, dropped


def title_of(merged: list[int]) -> str:
    return "Bundle: " + " ".join(f"#{n}" for n in merged)


def body_of(merged: list[int], heads: dict[int, str], left_out: dict[int, str]) -> str:
    parts = "\n".join(f"- #{n} @{heads[n][:9]}" for n in merged)
    out = "\n".join(f"- #{n}: {why}" for n, why in sorted(left_out.items())) or "- none"
    return (f"## Why\n\nThese PRs were each ready (green required checks, no conflict, auditor PASS for the head) and merge as one, with one CI run.\n\n"
            f"## What changed\n\nOne merge of each part's head, in PR number order:\n\n{parts}\n\nLeft out of the bundle:\n\n{out}\n\n"
            f"## Evidence\n\nEvery part passed tools/merge_ready.py for the head shown. The bundle's own CI is the check of the combination.\n\n"
            f"## Risk\n\nParts that touch the same code may fail together; if CI is red, rebuild with --without <PR>. "
            f"A part whose head moves after this is built is no longer what was checked: rebuild.\n")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="tools/bundle.sh", description=__doc__.split("\n")[0])
    ap.add_argument("--without", type=int, nargs="*", default=[], help="PRs to leave out (the part whose change broke CI)")
    ap.add_argument("--push", action="store_true", help="open the bundle PR (tools/pr-open.sh); otherwise only build the branch")
    a = ap.parse_args(argv[1:])
    try:
        ready, skipped = ready_prs(set(a.without))
        for n, why in skipped.items():
            print(f"bundle: #{n} is not ready: {why}")
        if len(ready) < MIN_PARTS:
            print(f"bundle: {len(ready)} ready ({', '.join('#' + str(n) for n in ready) or 'none'}); a bundle needs {MIN_PARTS}")
            return 0
        if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            raise RuntimeError("the work tree has uncommitted changes")
        start = git("branch", "--show-current").stdout.strip()
        detached = not start
        start = start or git("rev-parse", "HEAD").stdout.strip()
        if git("fetch", "-q", "origin", "main").returncode or git("switch", "-q", "-C", "bundle-wip", "origin/main").returncode:
            raise RuntimeError("cannot start a branch from origin/main")
        try:
            kept, left_out = fetch_heads(ready)
            merged, conflicted = merge_parts(kept)
            left_out.update(conflicted)
        except RuntimeError:
            git("merge", "--abort")
            abandon(start, detached)
            raise
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"bundle: nothing built: {e}", file=sys.stderr)
        return 1
    for n, why in sorted(left_out.items()):
        print(f"bundle: #{n} left out: {why}")
    if len(merged) < 2:
        print("bundle: fewer than 2 parts are left, so there is no bundle")
        abandon(start, detached)
        return 0
    branch = "bundle-" + "-".join(str(n) for n in merged)
    git("branch", "-M", branch)
    print(f"bundle: built {branch} with {', '.join('#' + str(n) for n in merged)}")
    if not a.push:
        return 0
    with tempfile.NamedTemporaryFile("w", suffix=".md") as f:
        f.write(body_of(merged, ready, left_out))
        f.flush()
        return subprocess.run([os.path.join(os.path.dirname(os.path.abspath(__file__)), "pr-open.sh"), "--title", title_of(merged),
                               "--body-file", f.name]).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv))
