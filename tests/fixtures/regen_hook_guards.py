"""Rewrite hook_guards.json from what the Python PreToolUse hook decides, after changing HOOK_GUARD_CASES or a guard:

    tools/test.sh tests/fixtures/regen_hook_guards.py
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent)]
import support  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    cases = support.hook_guard_verdicts(Path(td))
support.HOOK_GUARDS_FIXTURE.write_text(json.dumps({"cases": cases}, indent=1, ensure_ascii=True) + "\n")
print(f"wrote {support.HOOK_GUARDS_FIXTURE}")
