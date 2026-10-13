"""Which files the merger may write over a merge at land time without a PASS (`tools/land.sh --check --fix`), and
writing them. It needs nothing but the standard library: land.sh runs it as a script from main's checkout.

    python3 handline/infra/housekeeping.py apply <housekeeping.paths> <fix dir> <worktree>

writes each file under <fix dir> over <worktree> and prints its path, or names the first refusal and exits 1.
"""
from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path

# Read as instructions by every agent session in the repo, so a change to one always needs a PASS, whatever the
# list allows. Compared without case: the founder's disk does not tell CLAUDE.md from claude.md.
INSTRUCTION_FILES = {"claude.md", "claude.local.md", "agents.md"}
INSTRUCTION_DIRS = {".claude", ".git"}
INSTRUCTION_ROOTS = {"skill"}


def read_globs(text: str) -> list[str]:
    """The globs in a housekeeping.paths file: one per line, `#` starts a comment."""
    return [g for g in (line.split("#", 1)[0].strip() for line in text.splitlines()) if g]


def matches(path: str, glob: str) -> bool:
    """`glob` matched one path component at a time: `*` never crosses a `/`, and a `**` component stands for any
    number of whole components, none included. Case counts."""
    def walk(p: list[str], g: list[str]) -> bool:
        if not g:
            return not p
        if g[0] == "**":
            return any(walk(p[i:], g[1:]) for i in range(len(p) + 1))
        return bool(p) and fnmatch.fnmatchcase(p[0], g[0]) and walk(p[1:], g[1:])
    return walk(path.split("/"), glob.split("/"))


def refusal(path: str, globs: list[str]) -> str | None:
    """Why `path` may not be written without a PASS, or None."""
    parts = path.split("/")
    if not path or path.startswith("/") or "" in parts or "." in parts or ".." in parts or "\\" in path:
        return f"--fix {path}: not a path inside the repo"
    low = [p.lower() for p in parts]
    if low[-1] in INSTRUCTION_FILES or INSTRUCTION_DIRS & set(low) or low[0] in INSTRUCTION_ROOTS:
        return f"--fix {path}: agents read it as instructions; it needs a PASS"
    if not any(matches(path, g) for g in globs):
        return f"--fix {path}: not a housekeeping path (tools/housekeeping.paths); it needs a PASS"
    return None


def unsafe_dest(work: str, path: str) -> str | None:
    """Why writing `path` under the worktree `work` would land somewhere else, or None: a symlink on the way (the
    file itself included) is followed by a write, out of the worktree as whoever runs it."""
    here = Path(work)
    for part in path.split("/"):
        here = here / part
        if here.is_symlink():
            return f"--fix {path}: {here.relative_to(work)} is a symlink in the merge; not written through"
    root = os.path.realpath(work)
    if os.path.commonpath([root, os.path.realpath(Path(work) / path)]) != root:
        return f"--fix {path}: resolves outside the worktree"
    return None


def write(work: str, files: dict[str, bytes]) -> str | None:
    """Write each file over the worktree, checking every one before writing any. Why not, or None."""
    for path in files:
        why = unsafe_dest(work, path)
        if why:
            return why
    for path, data in files.items():
        dest = Path(work) / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return None


def _apply(paths_file: str, fix_dir: str, work: str) -> int:
    try:
        globs = read_globs(Path(paths_file).read_text(encoding="utf-8"))
    except OSError:
        globs = []
    root = Path(fix_dir)
    files = {}
    for f in sorted(root.rglob("*")):
        if f.is_symlink() or not f.is_file():
            if f.is_symlink():
                print(f"--fix {f.relative_to(root)}: a symlink, not a file", file=sys.stderr)
                return 1
            continue
        path = f.relative_to(root).as_posix()
        why = refusal(path, globs)
        if why:
            print(why, file=sys.stderr)
            return 1
        files[path] = f.read_bytes()
    why = write(work, files)
    if why:
        print(why, file=sys.stderr)
        return 1
    print("\n".join(files))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "apply":
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(_apply(*sys.argv[2:]))
