"""Rewrite claude_driver.json from what the Python Claude driver writes, after changing its cases or the driver:

    tools/test.sh tests/fixtures/regen_claude_driver.py
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent)]
import support  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    cases = support.claude_driver_cases(Path(td))
support.CLAUDE_DRIVER_FIXTURE.write_text(json.dumps(cases, indent=1, ensure_ascii=True) + "\n")
print(f"wrote {support.CLAUDE_DRIVER_FIXTURE}")
