"""Base drift (design section 8.8).

The direction matters and is easy to get backwards. `main..HEAD` lists the
agent's own commits, which says nothing about whether the base moved under it.
The base's movement is `HEAD..origin/<base>`.

The verdict is a file intersection, not a commit count. Fifty commits that
touch nothing the agent touched are irrelevant; one commit that touches its
files is not. Which of the two resume headers we emit follows from that
intersection, because telling an agent "your completed work is still valid"
when the base moved under those exact files is how work gets silently redone
or silently lost.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..infra import gitrun, textsafe

NO_OVERLAP_HEADER = (
    "The items under \"완료\" (done) below are already finished. Do not redo them."
)
OVERLAP_HEADER = (
    "The items under \"완료\" (done) below may no longer hold, because the base moved. "
    "Check the files they touch first."
)


@dataclass
class Drift:
    ok: bool = True
    base: str = ""
    behind: int = 0
    base_files: list[str] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)
    overlap: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def significant(self) -> bool:
        """Drift only matters where it touches the agent's own files."""
        return bool(self.overlap)

    def resume_header(self) -> str:
        """Which header to put above the completed items on resume.

        When drift could not be determined we emit the cautious header. Telling
        an agent its completed work is definitely still valid, on the strength
        of a check that failed, is the fail-open version of this and it loses
        or silently redoes work.
        """
        if not self.ok or self.significant:
            return OVERLAP_HEADER
        return NO_OVERLAP_HEADER

    def summary(self) -> str:
        if not self.ok:
            return f"drift unknown: {self.error}"
        if self.error:
            return f"{self.error}"
        if not self.behind:
            return f"base {self.base}: no drift"
        if self.significant:
            return (f"base {self.base}: {self.behind} commit(s) ahead, "
                    f"{len(self.overlap)} overlapping file(s): "
                    f"{', '.join(self.overlap[:5])}")
        return (f"base {self.base}: {self.behind} commit(s) ahead, "
                f"no overlap with this agent's files")


def _git(args: list[str], cwd: Path, timeout: int = 30) -> tuple[bool, str]:
    """On success, `out` is one line per record, each sanitised here, so a
    caller that embeds one is clean by construction. Split on "\n" only: that
    is git's separator, and a filename may hold anything else `splitlines`
    breaks on (U+0085, U+2028), which would make one file two. On failure it
    is git's own error text, sanitised the same way for `check` to embed into
    `d.error`."""
    rc, out, err = gitrun.run(cwd, *args, timeout=timeout)
    if rc != 0:
        return False, textsafe.oneline(err.strip())
    return True, "\n".join(textsafe.oneline(ln) for ln in out.strip().split("\n"))


def touched_files(repo: Path, base: str) -> list[str]:
    """Files this agent changed relative to the merge base: its own work."""
    ok, out = _git(["diff", "--name-only", f"origin/{base}...HEAD"], repo)
    if not ok:
        ok, out = _git(["diff", "--name-only", f"{base}...HEAD"], repo)
    files = [f for f in out.splitlines() if f] if ok else []
    ok, out = _git(["status", "--porcelain"], repo)
    if ok:
        files += [line[3:] for line in out.splitlines() if len(line) > 3]
    return sorted(set(files))


def check(repo: Path, base: str = "main", fetch: bool = True) -> Drift:
    d = Drift(base=base)
    if not (repo / ".git").exists():
        d.ok, d.error = False, f"{repo} is not a git repo"
        return d
    if fetch:
        ok, out = _git(["fetch", "--quiet"], repo, timeout=60)
        if not ok:
            # A stale origin ref is not a verdict. Marking this ok would hand
            # the agent the reassuring header on the strength of a check that
            # did not run, which is the fail-open case resume_header() exists
            # to avoid.
            d.ok = False
            d.error = f"fetch failed, origin/{base} may be out of date: {out[:120]}"

    ok, out = _git(["rev-list", "--count", f"HEAD..origin/{base}"], repo)
    if not ok:
        d.ok = False
        d.error = f"cannot compare against origin/{base}: {out[:160]}"
        return d
    d.behind = int(out or 0)

    if d.behind:
        ok, out = _git(["diff", "--name-only", f"HEAD...origin/{base}"], repo)
        d.base_files = [f for f in out.splitlines() if f] if ok else []

    d.touched = touched_files(repo, base)
    d.overlap = sorted(set(d.base_files) & set(d.touched))
    return d
