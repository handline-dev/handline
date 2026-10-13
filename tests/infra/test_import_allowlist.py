"""Tests: cli/ reaches state only through the daemon's client library (docs/daemon-plan.md, "Moving the
callers"), `infra.daemonclient`. A direct import of `db` or of a daemon module is refused unless it is
listed below. MOVING is today's list and shrinks as each command moves onto the client; KEPT names the few
that must work while the daemon is down, each with its reason. An entry nothing imports any more fails too,
so the lists only ever shrink.

Run on its own: tools/test.sh tests/infra/test_import_allowlist.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

# (file under handline/cli, module it imports) still to move onto the client library.
MOVING = {
    ("account.py", "handline.db"),
    ("agent.py", "handline.daemon.handoff"),
    ("agent.py", "handline.db"),
    ("ask.py", "handline.daemon.delivery"),
    ("ask.py", "handline.db"),
    ("daemon.py", "handline.daemon.locks"),
    ("daemon.py", "handline.daemon.loop"),
    ("daemon.py", "handline.db"),
    ("health.py", "handline.daemon.devmode"),
    ("health.py", "handline.db"),
    ("health.py", "handline.db.schema"),
    ("hr.py", "handline.db"),
    ("land.py", "handline.db"),
    ("task.py", "handline.db"),
}

# (file, module): why it stays. Only what has to run with the daemon down belongs here.
KEPT: dict[tuple[str, str], str] = {}


def imported(path: Path, root: Path) -> set[str]:
    """Every module `path` imports, written absolute; `from x import y` counts as x.y, since y may be a module."""
    pkg = list(path.relative_to(root).with_suffix("").parts[:-1])
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = pkg[:len(pkg) - node.level + 1] if node.level else []
            mod = base + (node.module.split(".") if node.module else [])
            out.update(".".join(mod + [a.name]) for a in node.names)
    return out


def internal(module: str) -> bool:
    return any(module == p or module.startswith(p + ".") for p in ("handline.db", "handline.daemon"))


def found(root: Path) -> set[tuple[str, str]]:
    """(file, module) for each internal import under root/handline/cli."""
    cli = root / "handline" / "cli"
    return {(str(f.relative_to(cli)), m) for f in sorted(cli.rglob("*.py")) for m in imported(f, root) if internal(m)}


def test_the_scan_sees_every_spelling_of_an_import(td: Path) -> None:
    pkg = td / "handline" / "cli"
    pkg.mkdir(parents=True)
    (pkg / "x.py").write_text(
        "from .. import db\nfrom ..db import schema\nfrom ..daemon import locks as dmn_locks\n"
        "import handline.daemon.loop\nfrom handline import daemon\n"
        "def f():\n    from ..daemon import states\n")
    got = {m for _, m in found(td)}
    check("scan: relative, absolute, aliased and in-function imports of db and daemon modules are all seen",
          got == {"handline.db", "handline.db.schema", "handline.daemon.locks", "handline.daemon.loop",
                  "handline.daemon", "handline.daemon.states"}, str(sorted(got)))


def test_cli_imports_only_what_is_listed() -> None:
    now = found(ROOT)
    listed = MOVING | set(KEPT)
    check("cli: no import of db or a daemon internal that is not listed (call the daemon through infra.daemonclient)",
          now <= listed, str(sorted(now - listed)))
    check("cli: every listed import is still made (strike an entry once its command has moved)",
          listed <= now, str(sorted(listed - now)))
    check("cli: an entry is either moving or kept, never both", not (MOVING & set(KEPT)), str(sorted(MOVING & set(KEPT))))
    check("cli: every kept import says why", all(r.strip() for r in KEPT.values()))


def main() -> int:
    def tests(td):
        run(lambda: test_the_scan_sees_every_spelling_of_an_import(Path(td)))
        run(lambda: test_cli_imports_only_what_is_listed())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
