"""Tests: `handline.infra.clipboard.copy`, the system clipboard write.

Run on its own: python3 tests/infra/test_clipboard.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import clipboard  # noqa: E402


def _with_tools(present: set[str], fail: bool = False):
    """Run `clipboard.copy("héllo")` as if only the tools in `present` were installed:
    (its result, the command it ran or None, the bytes it sent)."""
    ran: list = []
    real_which, real_run = clipboard.shutil.which, clipboard.subprocess.run

    def run(cmd, input=None, **kw):
        ran.append((cmd, input))
        if fail:
            raise subprocess.CalledProcessError(1, cmd)
    clipboard.shutil.which = lambda name: f"/bin/{name}" if name in present else None
    clipboard.subprocess.run = run
    try:
        result = clipboard.copy("héllo")
    finally:
        clipboard.shutil.which, clipboard.subprocess.run = real_which, real_run
    return result, (ran[0][0] if ran else None), (ran[0][1] if ran else None)


def test_copy_takes_the_first_tool_there_and_says_false_when_there_is_none_or_it_fails() -> None:
    ok, cmd, sent = _with_tools({"pbcopy", "xclip"})
    check("clipboard: pbcopy comes first", ok and cmd == ["pbcopy"], (ok, cmd))
    check("clipboard: the text goes in as UTF-8", sent == "héllo".encode("utf-8"), sent)
    ok, cmd, _ = _with_tools({"xsel", "clip.exe"})
    check("clipboard: the order goes on to xsel before clip.exe", ok and cmd[0] == "xsel", (ok, cmd))
    ok, cmd, _ = _with_tools({"wl-copy", "xclip"})
    check("clipboard: wl-copy comes before xclip", ok and cmd == ["wl-copy"], (ok, cmd))
    ok, cmd, _ = _with_tools({"xclip"})
    check("clipboard: xclip copies to the clipboard selection", ok and cmd == ["xclip", "-selection", "clipboard"], cmd)
    ok, cmd, _ = _with_tools(set())
    check("clipboard: no tool at all is False and runs nothing", ok is False and cmd is None, (ok, cmd))
    ok, cmd, _ = _with_tools({"pbcopy"}, fail=True)
    check("clipboard: a tool that fails is False", ok is False and cmd == ["pbcopy"], (ok, cmd))


def main() -> int:
    def tests(td):
        run(lambda: test_copy_takes_the_first_tool_there_and_says_false_when_there_is_none_or_it_fails())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
