"""Rewrite pyyaml_expected.json from the installed PyYAML.

Run where PyYAML is installed (`python3 -m pip install pyyaml`), after changing
DIFFERENTIAL_CASES, the dump lists, or `yamlcompat.dump`:

    python3 tests/fixtures/regen_pyyaml_expected.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent)]
import support  # noqa: E402

support.PYYAML_FIXTURE.write_text(
    json.dumps(support.pyyaml_expected(), indent=1, sort_keys=True, ensure_ascii=True) + "\n")
print(f"wrote {support.PYYAML_FIXTURE}")
