"""Tests: tools/migrate_home.py moves the home and its protected store, and undoes it.

Run on its own: python3 tests/infra/test_migrate_home.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.infra import protected  # noqa: E402

_spec = importlib.util.spec_from_file_location("migrate_home", ROOT / "tools" / "migrate_home.py")
mh = importlib.util.module_from_spec(_spec)
sys.modules["migrate_home"] = mh
_spec.loader.exec_module(mh)


def _tree(root: Path) -> dict:
    """Every file under root (links not followed) with its bytes."""
    out = {}
    for d, _dirs, files in os.walk(root, followlinks=False):
        for f in files:
            if f.startswith("db.sqlite"):      # created by the readers the test calls, not by the move
                continue
            p = Path(d) / f
            out[str(p.relative_to(root))] = p.read_bytes() if not p.is_symlink() else b"->" + os.readlink(p).encode()
    return out


def _fake_user(home: Path) -> mh.Plan:
    """A user folder holding .orcapipe (db, an agent, settings naming the home) and its protected store."""
    user = Path(os.path.realpath(home))
    old = user / ".orcapipe"
    (old / "agents" / "backend").mkdir(parents=True)
    (old / "agents" / "backend" / "agent.yaml").write_text("model: x\n")
    (old / "notes.bin").write_bytes(b"\x00data\x01")
    (old / "shared" / ".claude").mkdir(parents=True)
    (old / "shared" / ".claude" / "settings.json").write_text(json.dumps({
        "permissions": {"additionalDirectories": [str(old), "/other"]},
        "sandbox": {"filesystem": {"allowWrite": [str(user / "x" / ".git"), str(old)]},
                    "network": {"allowUnixSockets": [str(old / "daemon.sock")]}}}, indent=2) + "\n")
    p = mh.plan_for(user)
    p.store_old.mkdir(parents=True)
    (p.store_old / "hr-decisions").mkdir()
    (p.store_old / "hr-decisions" / "abc").write_text('{"call": "approved"}')
    return p


def test_the_home_and_its_protected_store_move_together_and_move_back(home: Path) -> None:
    p = _fake_user(home)
    never = lambda: False  # noqa: E731
    saved = {k: os.environ.get(k) for k in ("OCP_TEST_ROOT", "OCP_HOME", "HANDLINE_HOME")}
    os.environ["OCP_TEST_ROOT"], os.environ["OCP_HOME"] = str(p.user), str(p.old)
    os.environ.pop("HANDLINE_HOME", None)
    try:
        accounts.register("alpha")          # links its .claude into shared/, as every account has
        link = p.old / "accounts" / "alpha" / ".claude" / "settings.json"
        check("migrate: the fixture has an account link into the old home",
              link.is_symlink() and os.readlink(link) == str(p.old / "shared" / ".claude" / "settings.json"),
              str(os.readlink(link)) if link.is_symlink() else "no link")
        check("migrate: the script's store name is the one the code looks in",
              protected.root_for(p.old) == p.store_old, f"{protected.root_for(p.old)} vs {p.store_old}")
        before = _tree(p.user)
        check("migrate: a running daemon blocks it, and nothing changes",
              "daemon is running" in "; ".join(mh.problems(p, lambda: True)) and _tree(p.user) == before)
        try:
            mh.apply(p, lambda: True)
            refused = False
        except RuntimeError:
            refused = True
        check("migrate: apply refuses while the daemon runs", refused and _tree(p.user) == before)

        steps = mh.apply(p, never)
        check("migrate: the home is moved, a link is left, the store is renamed",
              p.new.is_dir() and p.old.is_symlink() and os.path.realpath(p.old) == str(p.new)
              and p.store_new.is_dir() and not p.store_old.exists() and len(steps) == 5, str(steps))
        os.environ["OCP_HOME"] = str(p.new)
        check("migrate: the founder's decision is still found under the new home",
              protected.get("hr-decisions", "abc") == '{"call": "approved"}')
        os.environ["OCP_HOME"] = str(p.old)
        check("migrate: and through the old path, which is now a link",
              paths.home() == p.new and protected.get("hr-decisions", "abc") == '{"call": "approved"}',
              str(paths.home()))
        settings = json.loads(p.settings.read_text())
        check("migrate: settings name the new path beside each old one and keep the rest",
              settings["permissions"]["additionalDirectories"] == [str(p.old), "/other", str(p.new)]
              and settings["sandbox"]["filesystem"]["allowWrite"] == [str(p.user / "x" / ".git"), str(p.old), str(p.new)]
              and settings["sandbox"]["network"]["allowUnixSockets"] == [str(p.old / "daemon.sock"), str(p.new / "daemon.sock")],
              json.dumps(settings))
        check("migrate: the original settings are kept", p.backup.is_file())
        os.environ["OCP_HOME"] = str(p.new)
        newlink = p.new / "accounts" / "alpha" / ".claude" / "settings.json"
        check("migrate: the account link now names the new path",
              os.readlink(newlink) == str(p.new / "shared" / ".claude" / "settings.json"), os.readlink(newlink))
        check("migrate: accounts.unlinked() finds nothing to repair", accounts.unlinked() == [], str(accounts.unlinked()))
        r = doctor.Report()
        doctor._check_accounts(r)
        check("migrate: doctor's account link check passes, no 'does not point at shared'",
              not any("does not point at shared" in row[1] for row in r.rows), str(r.rows))
        os.environ["OCP_HOME"] = str(p.old)
        try:
            mh.apply(p, never)
            again = False
        except RuntimeError:
            again = True
        check("migrate: a second apply is refused", again)

        try:
            mh.undo(p, lambda: True)
            undo_blocked = False
        except RuntimeError:
            undo_blocked = True
        check("migrate: undo refuses while the daemon runs, and changes nothing", undo_blocked and p.new.is_dir())
        undone = mh.undo(p, never)
        check("migrate: undo puts every file back exactly as it was",
              _tree(p.user) == before and not p.old.is_symlink() and not p.new.exists()
              and p.store_old.is_dir() and not p.store_new.exists(),
              str(sorted(set(_tree(p.user)) ^ set(before))) + str(undone))
        check("migrate: a second undo does nothing", mh.undo(p) == ["nothing to undo"] and _tree(p.user) == before)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_a_move_cut_short_is_undone_from_wherever_it_stopped(home: Path) -> None:
    p = _fake_user(home)
    before = _tree(p.user)
    os.rename(p.old, p.new)               # stopped right after the first step
    os.symlink(p.new, p.old)
    mh.undo(p)
    check("migrate: undo after only the first step restores the home", _tree(p.user) == before
          and not p.old.is_symlink())
    os.rename(p.old, p.new)               # stopped before the link
    mh.undo(p)
    check("migrate: undo with no link left restores the home too", _tree(p.user) == before)


def test_the_command_checks_without_changing_anything(home: Path) -> None:
    p = _fake_user(home)
    before = _tree(p.user)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "migrate_home.py"), "check", "--user-home", str(p.user)],
                       capture_output=True, text=True, timeout=60)
    check("migrate: check names the move and the store and changes nothing",
          r.returncode == 0 and ".orcapipe" in r.stdout and ".handline" in r.stdout and "store" in r.stdout and _tree(p.user) == before,
          r.stdout + r.stderr)


def main() -> int:
    def tests(td):
        run(lambda: test_the_home_and_its_protected_store_move_together_and_move_back(Path(td) / "mv"))
        run(lambda: test_a_move_cut_short_is_undone_from_wherever_it_stopped(Path(td) / "cut"))
        run(lambda: test_the_command_checks_without_changing_anything(Path(td) / "chk"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
