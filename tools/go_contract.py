#!/usr/bin/env python3
"""Write go/internal/rpc/contract.json, the method table the Go gateway checks before it relays a call:

    python3 tools/go_contract.py          write it
    python3 tools/go_contract.py --check  exit 1 if what is committed is stale (tests/infra/test_go_schema.py runs this)

For each method of contract/protocol.json: the lowest role that may call it (x-role) and, when the contract lists them,
the only roles that may (x-callers). The Go module cannot embed a file above it, so this is the copy it reads."""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
OUT = root / "go" / "internal" / "rpc" / "contract.json"


def table() -> str:
    spec = json.loads((root / "contract" / "protocol.json").read_text())
    out = {}
    for m in spec["methods"]:
        entry = {"role": m["x-role"]}
        if "x-callers" in m:
            entry["callers"] = m["x-callers"]
        out[m["name"]] = entry
    return json.dumps(out, indent=1, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    want = table()
    if "--check" in argv:
        if not OUT.is_file() or OUT.read_text() != want:
            print(f"{OUT.relative_to(root)} is stale: run python3 tools/go_contract.py")
            return 1
        return 0
    OUT.write_text(want)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
