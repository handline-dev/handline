#!/usr/bin/env python3
"""Check the Go daemon's migrations (go/internal/store/migrations/) are whole:

    python3 tools/go_schema.py --check  exit 1 if manifest.json and the .sql files disagree

The migrations are the schema's source. manifest.json lists (version, name, additive) for the Go store, which learns its steps
from it alone: versions run 1, 2, 3, ... and every step has its <version>_<name>.sql file, with no other .sql file beside them."""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "go" / "internal" / "store" / "migrations"


def problems() -> list[str]:
    steps = json.loads((OUT / "manifest.json").read_text())
    out = []
    for i, s in enumerate(steps, 1):
        if s["version"] != i:
            out.append(f"step {i} is numbered {s['version']}")
        if not (OUT / f"{s['version']:04d}_{s['name']}.sql").is_file():
            out.append(f"step {s['version']} ({s['name']}) has no sql file")
    named = {f"{s['version']:04d}_{s['name']}.sql" for s in steps}
    out += [f"{p.name} is in no manifest step" for p in sorted(OUT.glob("*.sql")) if p.name not in named]
    return out


def main(argv: list[str]) -> int:
    if "--check" not in argv:
        print(__doc__)
        return 2
    bad = problems()
    for line in bad:
        print(f"go_schema: {line}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
