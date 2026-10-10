"""Tests: the messages `hl venv install` gives.

Run on its own: python3 tests/infra/test_venv.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import venv  # noqa: E402


def test_a_failed_pip_run_names_its_error_not_its_retries():
    out = ("WARNING: The directory '/x/pip' is not writable.\n"
           "WARNING: Retrying (Retry(total=4)) after connection broken\n"
           "ERROR: Could not find a version that satisfies the requirement surely-not-a-package>=1.0\n"
           "ERROR: No matching distribution found for surely-not-a-package>=1.0\n")
    msg = venv.pip_error(out)
    check("venv: the error lines are kept",
          msg == "ERROR: Could not find a version that satisfies the requirement surely-not-a-package>=1.0 "
                 "ERROR: No matching distribution found for surely-not-a-package>=1.0", msg)
    check("venv: output with no ERROR line is kept whole", "just a crash" in venv.pip_error("just a crash\n"))


def main() -> int:
    def tests(td):
        run(lambda: test_a_failed_pip_run_names_its_error_not_its_retries())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
