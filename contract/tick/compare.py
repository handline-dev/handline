#!/usr/bin/env python3
"""Run the same tick fixtures through two daemons and report where their outputs differ.

  python3 contract/tick/compare.py "CMD_A" "CMD_B" [FIXTURE.json ...]

Each command is run as `CMD FIXTURE.json` and must print the output JSON (contract/tick/README.md) on
stdout; the Python daemon's is `python3 contract/tick/pyrun.py`. With no fixtures, every file in
contract/tick/fixtures. The report is deterministic: fixtures in name order, differences by path. Exit 0
when every fixture's two outputs are equal, 1 when any differs or a command failed.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(cmd: str, fixture: Path) -> dict:
    r = subprocess.run([*shlex.split(cmd), str(fixture)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"`{cmd}` exited {r.returncode}: {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)


def diff(a, b, path: str = "") -> list[str]:
    """Every place two JSON values differ, as `path: A != B`, in a stable order."""
    if type(a) is not type(b):
        return [f"{path or '.'}: {json.dumps(a, sort_keys=True)} != {json.dumps(b, sort_keys=True)}"]
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: missing from A, B has {json.dumps(b[k], sort_keys=True)}")
            elif k not in b:
                out.append(f"{path}.{k}: A has {json.dumps(a[k], sort_keys=True)}, missing from B")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list):
        out = [f"{path}: length {len(a)} != {len(b)}"] if len(a) != len(b) else []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [f"{path}: {json.dumps(a)} != {json.dumps(b)}"]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd_a, cmd_b = argv[0], argv[1]
    fixtures = [Path(p) for p in argv[2:]] or sorted((HERE / "fixtures").glob("*.json"))
    bad = 0
    for fx in fixtures:
        try:
            out_a, out_b = run(cmd_a, fx), run(cmd_b, fx)
        except (RuntimeError, ValueError) as e:
            print(f"ERROR {fx.stem}: {e}")
            bad += 1
            continue
        d = diff(out_a, out_b)
        print(f"{'SAME ' if not d else 'DIFF '} {fx.stem}")
        for line in d:
            print(f"        {line}")
        bad += bool(d)
    print(f"{len(fixtures) - bad} same, {bad} different")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
