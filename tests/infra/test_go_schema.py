"""Tests: the Go daemon's copies of Python facts are current: migrations from handline/db/schema.py, hook events from events.py.

Run on its own: python3 tests/infra/test_go_schema.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from handline.db import schema  # noqa: E402
from handline.session import events  # noqa: E402

MIGRATIONS = ROOT / "go" / "internal" / "store" / "migrations"


def test_generated_migrations_are_current() -> None:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "go_schema.py"), "--check"], capture_output=True, text=True)
    check("go schema: the committed migrations equal a fresh run of tools/go_schema.py", r.returncode == 0, r.stdout + r.stderr)


def test_go_knows_exactly_the_python_steps() -> None:
    known = [(s["version"], s["name"], s["additive"]) for s in json.loads((MIGRATIONS / "manifest.json").read_text())]
    check("go schema: the Go store's (version, name, additive) list equals schema.MIGRATIONS", known == [(v, n, bool(a)) for v, n, _, a in schema.MIGRATIONS],
          f"go {known} python {[(v, n, a) for v, n, _, a in schema.MIGRATIONS]}")
    check("go schema: every step has its sql file", all((MIGRATIONS / f"{v:04d}_{n}.sql").is_file() for v, n, _ in known))


def test_generated_contract_table_is_current() -> None:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "go_contract.py"), "--check"], capture_output=True, text=True)
    check("go contract: the committed method table equals a fresh run of tools/go_contract.py", r.returncode == 0, r.stdout + r.stderr)


def test_go_wants_exactly_the_hooks_python_installs() -> None:
    """Two hand copies of CAPTURED | ANSWERS | GUARDS: the launch keeps those hooks installed, and the
    settings page reads "installed" from them. An event added on one side only makes the page lie."""
    py = events.CAPTURED | events.ANSWERS | events.GUARDS
    for path, var in (("go/internal/claude/hooks.go", "Wanted"), ("go/internal/reads/settings.go", "hooksWanted")):
        m = re.search(rf"var {var} = \[\]string\{{(.*?)\}}", (ROOT / path).read_text(), re.S)
        go = set(re.findall(r'"(\w+)"', m.group(1))) if m else set()
        check(f"go hooks: {path} {var} equals CAPTURED | ANSWERS | GUARDS", go == py,
              f"Go only {sorted(go - py)}, Python only {sorted(py - go)}")


def main() -> int:
    def tests(td):
        run(lambda: test_generated_migrations_are_current())
        run(lambda: test_go_knows_exactly_the_python_steps())
        run(lambda: test_generated_contract_table_is_current())
        run(lambda: test_go_wants_exactly_the_hooks_python_installs())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
