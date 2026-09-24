"""Tests: `ocp backup` snapshots of OCP_HOME.

Run on its own: python3 tests/test_backup.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp import backup as backupmod  # noqa: E402


def _world(home: Path) -> Path:
    use_home(home)
    (home / "team.yaml").write_text("agents: {}\n")
    (home / "agents" / "a").mkdir(parents=True)
    (home / "agents" / "a" / "ROLE.md").write_text("# a\n")
    (home / "accounts" / "alpha" / ".claude").mkdir(parents=True)
    (home / "accounts" / "alpha" / ".claude" / ".credentials.json").write_text("secret")
    (home / "accounts" / "alpha" / "status.json").write_text("{}")
    (home / "venv" / "bin").mkdir(parents=True)
    (home / "venv" / "bin" / "python3").write_text("")
    con = db.connect()
    db.add_request(con, "human", "a", "hello", None, None, None, None, 0, None)
    con.close()
    return home


def _names(tar_path: Path) -> set[str]:
    import tarfile
    with tarfile.open(tar_path) as tar:
        return set(tar.getnames())


def test_a_snapshot_holds_the_state_and_never_a_login(home: Path) -> None:
    _world(home)
    dest = home.parent / "bk"
    out = backupmod.snapshot(home, dest, now=1_000_000)
    names = _names(out)
    check("backup: team.yaml, a role and an account's status are kept",
          {"team.yaml", "agents/a/ROLE.md", "accounts/alpha/status.json"} <= names, str(names))
    check("backup: nothing under accounts/*/.claude is copied",
          not any(".claude" in n for n in names), str(names))
    check("backup: venv is left out", not any(n.startswith("venv") for n in names))
    import tarfile
    with tarfile.open(out) as tar:
        tar.extract("db.sqlite", home.parent / "restored")
    con = sqlite3.connect(home.parent / "restored" / "db.sqlite")
    body = con.execute("select body from requests").fetchone()[0]
    con.close()
    check("backup: the database copy holds its rows", body == "hello", body)


def test_an_unchanged_home_writes_no_second_snapshot(home: Path) -> None:
    _world(home)
    dest = home.parent / "bk"
    first = backupmod.snapshot(home, dest, now=1_000_000)
    check("backup: nothing changed, nothing written",
          backupmod.snapshot(home, dest, now=1_000_300) is None)
    con = db.connect()
    db.add_request(con, "human", "a", "again", None, None, None, None, 0, None)
    con.close()
    second = backupmod.snapshot(home, dest, now=1_000_600)
    check("backup: a new database row is a change", second is not None and second != first)
    (home / "team.yaml").write_text("agents: {x: {}}\n")
    check("backup: an edited file is a change",
          backupmod.snapshot(home, dest, now=1_000_900) is not None)


def test_logs_locks_and_status_files_are_not_a_change(home: Path) -> None:
    _world(home)
    (home / "logs").mkdir()
    (home / "logs" / "backup.log").write_text("wrote ...\n")
    (home / "locks").mkdir()
    (home / "locks" / "daemon.lock").write_text("123")
    dest = home.parent / "bk"
    names = _names(backupmod.snapshot(home, dest, now=1_000_000))
    check("backup: logs/ and locks/ are not in a snapshot",
          not any(n.startswith(("logs", "locks")) for n in names), str(names))
    (home / "logs" / "backup.log").write_text("wrote ...\nunchanged ...\n")
    (home / "locks" / "daemon.lock").write_text("456")
    (home / "accounts" / "alpha" / "status.json").write_text('{"pct_5h": 9}')
    check("backup: a log line, a lock and a status.json alone write no snapshot",
          backupmod.snapshot(home, dest, now=1_000_300) is None)

def test_prune_keeps_the_last_day_then_one_a_day(home: Path) -> None:
    dest = home / "bk"
    dest.mkdir(parents=True)
    now = time.mktime((2026, 9, 24, 12, 0, 0, 0, 0, -1))
    ages_h = [1, 20, 30, 31, 55, 24 * 40]
    made = {}
    for h in ages_h:
        t = now - h * 3600
        p = dest / (backupmod.PREFIX + time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + backupmod.SUFFIX)
        p.write_text("")
        os.utime(p, (t, t))
        made[h] = p
    backupmod.prune(dest, now)
    kept = {h for h, p in made.items() if p.exists()}
    check("backup: every snapshot of the last day is kept", {1, 20} <= kept, str(kept))
    check("backup: an older day keeps only its first snapshot",
          (31 in kept) and (30 not in kept), str(kept))
    check("backup: past 30 days nothing is kept", 24 * 40 not in kept, str(kept))


def test_the_backups_live_beside_the_home_not_in_it(home: Path) -> None:
    check("backup: the folder is a sibling of OCP_HOME",
          backupmod.backup_dir(home) == home.with_name(home.name + "-backups"))
    plist = backupmod.build_plist(home)
    check("backup: launchd runs `ocp backup` every five minutes",
          plist["ProgramArguments"][-1] == "backup" and plist["StartInterval"] == 300)
    check("backup: the plist lands in the stand-in home under the suite",
          TEST_ROOT in backupmod.plist_path().parents)


def main() -> int:
    def tests(td):
        run(lambda: test_a_snapshot_holds_the_state_and_never_a_login(Path(td) / 'b1' / 'home'))
        run(lambda: test_an_unchanged_home_writes_no_second_snapshot(Path(td) / 'b2' / 'home'))
        run(lambda: test_logs_locks_and_status_files_are_not_a_change(Path(td) / 'b5' / 'home'))
        run(lambda: test_prune_keeps_the_last_day_then_one_a_day(Path(td) / 'b3'))
        run(lambda: test_the_backups_live_beside_the_home_not_in_it(Path(td) / 'b4' / 'home'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
