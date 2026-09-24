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

def _snapshots(dest: Path, now: float, ages_s: list[int]) -> dict[int, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    made = {}
    for a in ages_s:
        t = now - a
        p = dest / (backupmod.PREFIX + time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + backupmod.SUFFIX)
        p.write_text("")
        os.utime(p, (t, t))
        made[a] = p
    return made


def test_prune_keeps_the_newest_and_one_near_each_rung(home: Path) -> None:
    now = time.mktime((2026, 9, 24, 12, 0, 0, 0, 0, -1))
    m, h, d = 60, 3600, 86400
    # Two candidates near every rung, one far from all, and two beyond 30 days.
    ages = [10, 50, 4 * m, 6 * m, 22 * m, 35 * m, 45 * m, 62 * m, 5 * h, 7 * h,
            20 * h, 26 * h, 6 * d, 8 * d, 29 * d, 29 * d + h, 31 * d, 90 * d]
    made = _snapshots(home / "bk", now, ages)
    backupmod.prune(home / "bk", now)
    kept = {a for a, p in made.items() if p.exists()}
    check("backup: the newest snapshot is kept", 10 in kept, str(sorted(kept)))
    check("backup: the 1m rung keeps the snapshot nearest a minute old (50s, not 10s)",
          50 in kept, str(sorted(kept)))
    check("backup: the 5m rung keeps the one nearest five minutes",
          4 * m in kept and 6 * m not in kept, str(sorted(kept)))
    check("backup: the 30m rung keeps the one nearest half an hour",
          35 * m in kept and 22 * m not in kept, str(sorted(kept)))
    check("backup: the 1h rung keeps the one nearest an hour",
          62 * m in kept and 45 * m not in kept, str(sorted(kept)))
    check("backup: the 6h rung keeps the one nearest six hours",
          5 * h in kept and 7 * h not in kept, str(sorted(kept)))
    check("backup: the 1d rung keeps the one nearest a day",
          26 * h in kept and 20 * h not in kept, str(sorted(kept)))
    check("backup: the 7d rung keeps the one nearest a week",
          6 * d in kept and 8 * d not in kept, str(sorted(kept)))
    check("backup: the 30d rung keeps the one nearest thirty days",
          29 * d + h in kept and 29 * d not in kept, str(sorted(kept)))
    check("backup: nothing older than thirty days is kept",
          31 * d not in kept and 90 * d not in kept, str(sorted(kept)))
    check("backup: a second prune removes nothing more",
          backupmod.prune(home / "bk", now) == [])


def test_prune_keeps_the_newest_even_when_it_is_old(home: Path) -> None:
    now = time.mktime((2026, 9, 24, 12, 0, 0, 0, 0, -1))
    made = _snapshots(home / "bk", now, [40 * 86400, 60 * 86400])
    backupmod.prune(home / "bk", now)
    check("backup: with only old snapshots the newest survives, the rest go",
          made[40 * 86400].exists() and not made[60 * 86400].exists())
    check("backup: an empty folder prunes to nothing", backupmod.prune(home / "none", now) == [])


def test_a_minute_of_snapshots_for_days_stays_bounded(home: Path) -> None:
    now = time.mktime((2026, 9, 24, 12, 0, 0, 0, 0, -1))
    dest = home / "bk"
    # Replay the clock: a snapshot every minute for three days, pruning each time.
    for minute in range(3 * 24 * 60, -1, -1):
        _snapshots(dest, now - minute * 60, [0])
        backupmod.prune(dest, now - minute * 60)
    n = len(list(dest.glob(backupmod.PREFIX + "*" + backupmod.SUFFIX)))
    check("backup: three days of one-a-minute leave at most one per rung and the newest",
          n <= len(backupmod.LADDER_S) + 1, str(n))


def test_the_backups_live_beside_the_home_not_in_it(home: Path) -> None:
    check("backup: the folder is a sibling of OCP_HOME",
          backupmod.backup_dir(home) == home.with_name(home.name + "-backups"))
    plist = backupmod.build_plist(home)
    check("backup: launchd runs `ocp backup` every minute",
          plist["ProgramArguments"][-1] == "backup" and plist["StartInterval"] == 60)
    check("backup: the plist lands in the stand-in home under the suite",
          TEST_ROOT in backupmod.plist_path().parents)


def main() -> int:
    def tests(td):
        run(lambda: test_a_snapshot_holds_the_state_and_never_a_login(Path(td) / 'b1' / 'home'))
        run(lambda: test_an_unchanged_home_writes_no_second_snapshot(Path(td) / 'b2' / 'home'))
        run(lambda: test_logs_locks_and_status_files_are_not_a_change(Path(td) / 'b5' / 'home'))
        run(lambda: test_prune_keeps_the_newest_and_one_near_each_rung(Path(td) / 'b3'))
        run(lambda: test_prune_keeps_the_newest_even_when_it_is_old(Path(td) / 'b6'))
        run(lambda: test_a_minute_of_snapshots_for_days_stays_bounded(Path(td) / 'b7'))
        run(lambda: test_the_backups_live_beside_the_home_not_in_it(Path(td) / 'b4' / 'home'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
