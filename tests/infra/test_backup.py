"""Tests: `ocp backup` snapshots of OCP_HOME.

Run on its own: python3 tests/infra/test_backup.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.infra import backup as backupmod  # noqa: E402


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

def _partials(dest: Path) -> list[Path]:
    return sorted(dest.glob("*.partial"))


def test_a_file_that_vanishes_or_a_failed_write_leaves_no_partial(home: Path) -> None:
    """A snapshot is written while the home is live: a lock or a status.json
    that is replaced between the walk and the read raised FileNotFoundError,
    and the half-written .partial stayed for ever (prune only sees *.tar.gz)."""
    _world(home)
    dest = home.parent / "bk"
    real_files = backupmod._files
    backupmod._files = lambda h: real_files(h) + [Path("gone.json")]
    try:
        out = backupmod.snapshot(home, dest, now=1_000_000)
    finally:
        backupmod._files = real_files
    check("backup: a file that vanished mid-walk does not fail the snapshot", out is not None)
    names = _names(out)
    check("backup: it is left out and the rest is kept",
          "gone.json" not in names and "team.yaml" in names, str(names))
    check("backup: no partial is left behind", _partials(dest) == [], str(_partials(dest)))

    # Any other failure: the partial is removed, nothing is recorded as taken,
    # so the next run tries again.
    (home / "team.yaml").write_text("agents: {y: {}}\n")
    import tarfile
    real_add = tarfile.TarFile.add

    def broken(self, *a, **kw):
        raise OSError("disk full")
    tarfile.TarFile.add = broken
    try:
        try:
            backupmod.snapshot(home, dest, now=1_000_300)
            raised = False
        except OSError:
            raised = True
    finally:
        tarfile.TarFile.add = real_add
    check("backup: a write that fails raises", raised)
    check("backup: and leaves no partial", _partials(dest) == [], str(_partials(dest)))
    check("backup: nor a record that it was taken",
          backupmod.snapshot(home, dest, now=1_000_600) is not None)


def test_a_concurrent_prune_taking_the_same_file_first_is_not_an_error(home: Path) -> None:
    """Two prune passes can race on the same expired file: the other's unlink
    can land before this one's stat, or between its stat and its unlink. Each
    is forced here, so neither depends on the order the glob returns files."""
    now = time.mktime((2026, 9, 24, 12, 0, 0, 0, 0, -1))
    real_stat, real_expired = Path.stat, backupmod.expired

    for label, race in (("before its stat", "stat"), ("between its stat and its unlink", "unlink")):
        dest = home / f"bk-{race}"
        made = _snapshots(dest, now, [0, 60 * 86400, 90 * 86400])
        kept, b = made[0], made[90 * 86400]

        def stat_after_theirs(self, *a, **kw):
            if self == b:
                b.unlink(missing_ok=True)
            return real_stat(self, *a, **kw)

        def expired_after_theirs(times, at):
            b.unlink(missing_ok=True)
            return real_expired(times, at)

        if race == "stat":
            Path.stat = stat_after_theirs
        else:
            backupmod.expired = expired_after_theirs
        try:
            backupmod.prune(dest, now)
        except Exception as e:  # noqa: BLE001
            check(f"backup: a file taken {label} raises nothing", False, repr(e))
        finally:
            Path.stat = real_stat
            backupmod.expired = real_expired
        check(f"backup: a file taken {label} raises nothing and is gone either way", not b.exists())
        check(f"backup: the survivor is untouched ({label})", kept.exists())


def test_a_partial_from_a_killed_run_is_pruned_once_stale(home: Path) -> None:
    dest = home / "bk"
    dest.mkdir(parents=True)
    now = 1_700_000_000.0
    old, young = dest / "orcapipe-a.partial", dest / "orcapipe-b.partial"
    for f, age in ((old, backupmod.PARTIAL_STALE_S + 60), (young, 5)):
        f.write_text("")
        os.utime(f, (now - age, now - age))
    backupmod.prune(dest, now)
    check("backup: a partial older than any live run is removed", not old.exists())
    check("backup: one a run may still be writing is kept", young.exists())


def test_two_snapshots_in_one_second_keep_both(home: Path) -> None:
    """Names have one-second resolution, and a rename over an existing name
    replaced it silently: a manual `ocp backup` racing launchd lost a snapshot."""
    _world(home)
    dest = home.parent / "bk"
    first = backupmod.snapshot(home, dest, now=1_000_000)
    body = first.read_bytes()
    (home / "team.yaml").write_text("agents: {z: {}}\n")
    second = backupmod.snapshot(home, dest, now=1_000_000)
    check("backup: a second snapshot in the same second is a different file",
          second is not None and second != first and second.exists(), str((first, second)))
    check("backup: the first is untouched", first.read_bytes() == body)
    (home / "team.yaml").write_text("agents: {w: {}}\n")
    third = backupmod.snapshot(home, dest, now=1_000_000)
    # (Retention may drop the middle one by now - what matters is no reuse.)
    check("backup: and a third gets its own name too",
          len({first, second, third}) == 3 and third.exists() and first.exists())
    check("backup: every one is a snapshot prune knows",
          all(p.name.startswith(backupmod.PREFIX) and p.name.endswith(backupmod.SUFFIX)
              for p in (first, second, third)))
    check("backup: no partial is left", _partials(dest) == [])


def test_install_boots_out_then_bootstraps_and_reports_a_refusal(home: Path) -> None:
    """The launchctl path had no test. Every call goes through
    service.run_launchctl, so replacing that name fakes the whole of it."""
    from ocp.infra import service
    use_home(home)

    class Res:
        def __init__(self, code=0, out="", err=""):
            self.returncode, self.stdout, self.stderr = code, out, err

    calls: list[list[str]] = []
    answer = {"bootstrap": Res()}

    def fake(args):
        calls.append(args)
        return answer.get(args[0], Res())

    real = service.run_launchctl
    service.run_launchctl = fake
    try:
        ok, msg = backupmod.install(home)
        check("backup install: succeeds when launchctl does", ok, msg)
        check("backup install: boots any old job out, then bootstraps the plist",
              [c[0] for c in calls] == ["bootout", "bootstrap"]
              and calls[0][1].endswith("/" + backupmod.LABEL)
              and calls[1][1:] == [service.domain_target(), str(backupmod.plist_path())],
              str(calls))
        import plistlib
        with open(backupmod.plist_path(), "rb") as f:
            plist = plistlib.load(f)
        check("backup install: the plist written is the built one",
              plist == backupmod.build_plist(home), str(plist))
        check("backup install: and it lands in the stand-in home, not the real one",
              TEST_ROOT in backupmod.plist_path().parents)
        check("backup install: the message names the interval and the folder",
              f"{backupmod.INTERVAL_S}s" in msg and str(backupmod.backup_dir(home)) in msg, msg)

        calls.clear()
        answer["bootstrap"] = Res(5, "", "Bootstrap failed: 5: Input/output error\n")
        ok, msg = backupmod.install(home)
        check("backup install: a refused bootstrap is reported, with launchctl's words",
              not ok and "Input/output error" in msg, msg)
        answer["bootstrap"] = Res(1, "", "")
        ok, msg = backupmod.install(home)
        check("backup install: a refusal that says nothing still says something",
              not ok and msg, msg)
    finally:
        service.run_launchctl = real


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


MIN = 60


def _replay(days: int, every: int = MIN) -> tuple[list[float], float]:
    """A snapshot every `every` seconds for `days`, pruned after each one - the
    way launchd runs it - on a fake clock. Returns what is left and the time."""
    t0 = 1_700_000_000.0
    have: list[float] = []
    now = t0
    while now < t0 + days * 86400:
        have.append(now)
        have = [t for t in have if t not in backupmod.expired(have, now)]
        now += every
    return have, now - every


def test_the_ladder_keeps_something_near_every_rung_over_a_month(home: Path) -> None:
    have, now = _replay(40)
    ages = sorted(now - t for t in have)
    rungs = [(m * MIN, w) for m, w in
             ((1, 1), (5, 1), (30, 5), (60, 30), (360, 60), (1440, 360), (10080, 1440),
              (43200, 10080))]
    check("backup: the newest snapshot is kept", ages[0] == 0, str(ages[:3]))
    for rung, width in rungs:
        near = min(abs(a - rung) for a in ages)
        check(f"backup: over 40 days a snapshot is kept within {width}m of the {rung // MIN}m rung",
              near <= width * MIN, f"nearest is {near / MIN:.0f}m off; kept ages (m): "
              f"{[round(a / MIN) for a in ages]}")
    check("backup: nothing is kept past thirty days",
          ages[-1] <= backupmod.MAX_AGE_S, str(ages[-1] / 86400))
    check("backup: the history stays small - a few dozen snapshots, not a day of minutes",
          len(have) <= 60, str(len(have)))


def test_the_ladder_has_no_gap_at_any_point_in_its_history(home: Path) -> None:
    """The failure a fixed-set test cannot see: a rule that re-picks the nearest
    to each rung on every run deletes what sits between two rungs before it can
    age into the next. It looked right after a few minutes and was hollow
    after 33 hours."""
    for days in (1.375, 3, 10, 36):
        have, now = _replay(days)
        ages = [now - t for t in have]
        rungs = [r * MIN for r in (1, 5, 30, 60, 360, 1440, 10080, 43200) if r * MIN <= days * 86400 * 0.8]
        for rung in rungs:
            # Slot width at this rung is at most a third of it, and never more than a day.
            near = min(abs(a - rung) for a in ages)
            check(f"backup: after {days} days something is within reach of the {rung // MIN}m rung",
                  near <= max(rung / 3, MIN), f"{near / MIN:.0f}m off")


def test_prune_reads_the_files_and_deletes_only_what_the_ladder_lets_go(home: Path) -> None:
    now = time.mktime((2026, 9, 24, 12, 0, 0, 0, 0, -1))
    ages = [0, 60, 90 * 86400, 31 * 86400]
    made = _snapshots(home / "bk", now, ages)
    (home / "bk" / "notes.txt").write_text("not a snapshot")
    removed = backupmod.prune(home / "bk", now)
    check("backup: files past thirty days go, recent ones stay",
          [p.exists() for p in made.values()] == [True, True, False, False], str(removed))
    check("backup: a file that is not a snapshot is left alone",
          (home / "bk" / "notes.txt").exists())
    check("backup: with only old snapshots the newest survives",
          backupmod.expired([now - 40 * 86400, now - 60 * 86400], now) == {now - 60 * 86400})
    check("backup: an empty folder prunes to nothing", backupmod.prune(home / "none", now) == [])


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
        run(lambda: test_the_ladder_keeps_something_near_every_rung_over_a_month(Path(td) / 'b3'))
        run(lambda: test_the_ladder_has_no_gap_at_any_point_in_its_history(Path(td) / 'b6'))
        run(lambda: test_prune_reads_the_files_and_deletes_only_what_the_ladder_lets_go(Path(td) / 'b7'))
        run(lambda: test_a_concurrent_prune_taking_the_same_file_first_is_not_an_error(Path(td) / 'b12'))
        run(lambda: test_a_file_that_vanishes_or_a_failed_write_leaves_no_partial(Path(td) / 'b8' / 'home'))
        run(lambda: test_a_partial_from_a_killed_run_is_pruned_once_stale(Path(td) / 'b9'))
        run(lambda: test_two_snapshots_in_one_second_keep_both(Path(td) / 'b10' / 'home'))
        run(lambda: test_install_boots_out_then_bootstraps_and_reports_a_refusal(Path(td) / 'b11' / 'home'))
        run(lambda: test_the_backups_live_beside_the_home_not_in_it(Path(td) / 'b4' / 'home'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
