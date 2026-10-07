"""Tests: the schema is versioned. An old database migrates once, however many processes open it; a
newer one is refused unless every step it has that this code lacks only added; one the current code made is accepted untouched; `connect` migrates nothing that
has data.

Run on its own: tools/test.sh tests/infra/test_schema_versions.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.db import schema as dbschema  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent


def _old_database(p: Path) -> None:
    """A database from before versions: the tables, no schema_version, one request in the old state."""
    con = dbschema.connect(p)
    con.execute("DROP TABLE schema_version")
    con.execute("INSERT INTO requests(ts, requester, target, kind, state, body) VALUES(1, 'a', 'b', 'say', 'queued', 'x')")
    con.close()


def _versions(p: Path) -> list[int]:
    con = sqlite3.connect(p)
    try:
        return [r[0] for r in con.execute("SELECT version FROM schema_version ORDER BY version")]
    finally:
        con.close()


def test_a_new_database_is_stamped_and_a_second_connect_changes_nothing(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "new.db"
    dbschema.connect(p).close()
    check("new: stamped with every migration, in order", _versions(p) == list(range(1, dbschema.LATEST_VERSION + 1)))
    stamp = sqlite3.connect(p).execute("SELECT applied_at FROM schema_version").fetchall()
    dbschema.connect(p).close()
    check("new: a second connect writes nothing", sqlite3.connect(p).execute(
        "SELECT applied_at FROM schema_version").fetchall() == stamp)


def _with_steps(*extra):
    """The real migrations with `extra` steps, not additive, after them; returns what to put back."""
    real = dbschema.MIGRATIONS, dbschema.LATEST_VERSION
    dbschema.MIGRATIONS = list(dbschema.MIGRATIONS) + [(real[1] + 1 + i, f"fake{i}", fn, False) for i, fn in enumerate(extra)]
    dbschema.LATEST_VERSION = dbschema.MIGRATIONS[-1][0]
    return real


def _restore(real) -> None:
    dbschema.MIGRATIONS, dbschema.LATEST_VERSION = real


def test_a_plain_connect_runs_the_additive_steps_and_stops_at_one_that_is_not(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "old.db"
    _old_database(p)
    check("old: it starts with no versions", not _has_table(p))
    additive = dbschema.LATEST_VERSION
    ran = []
    real = _with_steps(lambda c: ran.append(1))
    try:
        try:
            dbschema.connect(p)
            refused = ""
        except dbschema.SchemaError as e:
            refused = str(e)
    finally:
        _restore(real)
    check("old: connect ran every additive step, stamped them, and not the one that is not",
          _versions(p) == list(range(1, additive + 1)) and not ran, str(_versions(p)))
    check("old: and its data is still there", sqlite3.connect(p).execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1)
    check("old: the step that is not additive is refused, naming a command that exists",
          "older" in refused and f"`{dbschema.RESTART_COMMAND}`" in refused, refused)


def test_a_database_one_additive_step_behind_is_brought_up_by_a_plain_connect(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "behind.db"
    dbschema.connect(p).close()
    raw = sqlite3.connect(p)
    raw.execute("DROP TABLE rpc_replies")
    raw.execute("DELETE FROM schema_version WHERE version > 1")
    raw.commit()
    raw.close()
    con = dbschema.connect(p)
    check("behind: a version-1 database with data ends at the latest step, with its table",
          dbschema.schema_version(con) == dbschema.LATEST_VERSION
          and con.execute("SELECT 1 FROM sqlite_master WHERE name = 'rpc_replies'").fetchone() is not None)
    con.close()


def test_a_database_made_before_the_extra_worktrees_column_gets_it_on_connect(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "nocol.db"
    dbschema.connect(p).close()
    raw = sqlite3.connect(p)
    raw.execute("ALTER TABLE agent_state DROP COLUMN extra_worktrees")
    raw.execute("DELETE FROM schema_version WHERE version >= 10")
    raw.commit()
    raw.close()
    con = dbschema.connect(p)
    check("extra_worktrees: a database at the step before has the column after a plain connect",
          "extra_worktrees" in {r[1] for r in con.execute("PRAGMA table_info(agent_state)")})
    con.close()


def test_the_command_a_refusal_names_parses(home: Path) -> None:
    from handline.cli import parser as parsermod
    cmd = dbschema.RESTART_COMMAND
    try:
        parsermod.build_parser().parse_args(cmd.split()[1:])
        ok = True
    except SystemExit:
        ok = False
    check("refusal: the command it names is one the CLI parses", ok, cmd)


def test_the_daemon_migrates_a_database_waiting_for_a_step_once(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "step.db"
    _old_database(p)
    ran = []
    real = _with_steps(lambda c: ran.append(1))
    try:
        dbschema.connect(p, migrating=True).close()
        dbschema.connect(p).close()          # a plain connect now takes it
        dbschema.connect(p, migrating=True).close()
    finally:
        _restore(real)
    check("step: the daemon's connect runs it once and a plain connect then takes the database", ran == [1], str(ran))


def test_a_migrator_that_holds_the_lock_too_long_is_named(home: Path) -> None:
    import fcntl
    home.mkdir(parents=True, exist_ok=True)
    p = home / "held.db"
    _old_database(p)
    holder = open(str(p) + ".migrate-lock", "w")
    fcntl.flock(holder, fcntl.LOCK_EX)
    holder.write("4242")
    holder.flush()
    real_wait, dbschema.MIGRATE_WAIT_S = dbschema.MIGRATE_WAIT_S, 0.3
    try:
        try:
            dbschema.connect(p, migrating=True)
            msg = ""
        except dbschema.SchemaError as e:
            msg = str(e)
    finally:
        dbschema.MIGRATE_WAIT_S = real_wait
        holder.close()
    check("lock: a wait past the limit is an error naming the holder", "4242" in msg and "busy migrating" in msg, msg)
    dbschema.connect(p, migrating=True).close()      # and the lock is free again once it lets go


def _has_table(p: Path) -> bool:
    return bool(sqlite3.connect(p).execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_version'").fetchone())


def test_a_newer_database_is_refused_by_connect_and_by_migrate(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "newer.db"
    dbschema.connect(p).close()
    raw = sqlite3.connect(p)
    raw.execute("INSERT INTO schema_version(version, name, applied_at) VALUES(?, 'from the future', 0)",
                (dbschema.LATEST_VERSION + 1,))
    raw.commit()
    raw.close()
    for label, opener in (("connect", lambda: dbschema.connect(p)),
                          ("the daemon's connect", lambda: dbschema.connect(p, migrating=True))):
        try:
            opener()
            msg = ""
        except dbschema.SchemaError as e:
            msg = str(e)
        check(f"newer: {label} refuses it, naming both versions", "newer" in msg and str(dbschema.LATEST_VERSION + 1) in msg, msg)


def _stamp_future(p: Path, additive: bool) -> None:
    """What code one step ahead leaves behind: a step this code does not know, with its column."""
    raw = sqlite3.connect(p)
    dbschema._mark_additive_column(raw)
    raw.execute("INSERT INTO schema_version(version, name, applied_at, additive) VALUES(?, 'ahead', 0, ?)",
                (dbschema.LATEST_VERSION + 1, int(additive)))
    raw.commit()
    raw.close()


def test_a_database_an_additive_step_ahead_is_used_as_it_is(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "ahead.db"
    dbschema.connect(p).close()
    _stamp_future(p, additive=True)
    for label, opener in (("connect", lambda: dbschema.connect(p)),
                          ("the daemon's connect", lambda: dbschema.connect(p, migrating=True))):
        try:
            opener().close()
            msg = ""
        except dbschema.SchemaError as e:
            msg = str(e)
        check(f"ahead: {label} accepts a step that only added", not msg, msg)
    check("ahead: and nothing was stamped or removed", _versions(p) == list(range(1, dbschema.LATEST_VERSION + 2)))


def test_a_database_a_step_ahead_that_is_not_additive_is_refused(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "ahead2.db"
    dbschema.connect(p).close()
    _stamp_future(p, additive=False)
    try:
        dbschema.connect(p)
        msg = ""
    except dbschema.SchemaError as e:
        msg = str(e)
    check("ahead: a step that was not additive is refused", "newer" in msg, msg)


def test_the_additive_mark_is_stamped_and_an_older_stamp_is_filled_in(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "mark.db"
    dbschema.connect(p).close()
    marks = lambda: dict(sqlite3.connect(p).execute("SELECT version, additive FROM schema_version"))
    check("mark: a step is stamped with whether it is additive",
          marks() == {v: int(a) for v, _n, _s, a in dbschema.MIGRATIONS}, str(marks()))
    raw = sqlite3.connect(p)
    raw.execute("ALTER TABLE schema_version DROP COLUMN additive")
    raw.commit()
    raw.close()
    dbschema.connect(p).close()
    check("mark: a database stamped before the column is not touched by a connect that has nothing to run",
          "additive" not in {r[1] for r in sqlite3.connect(p).execute("PRAGMA table_info(schema_version)")})
    raw = sqlite3.connect(p)
    raw.execute("DELETE FROM schema_version WHERE version = ?", (dbschema.LATEST_VERSION,))
    raw.commit()
    raw.close()
    dbschema.connect(p).close()
    check("mark: the next step run fills it in for the steps before",
          marks() == {v: int(a) for v, _n, _s, a in dbschema.MIGRATIONS}, str(marks()))


def test_only_the_checkout_the_service_runs_may_migrate_the_live_home(home: Path) -> None:
    import plistlib
    from handline.infra import paths, service
    home.mkdir(parents=True, exist_ok=True)
    live = home / "live"
    live.mkdir()
    p = live / "db.sqlite"
    plist = home / "service.plist"

    def owed() -> None:
        """The database one step short, as a checkout with a newer step finds it."""
        raw = sqlite3.connect(p)
        raw.execute("DELETE FROM schema_version WHERE version = ?", (dbschema.LATEST_VERSION,))
        raw.commit()
        raw.close()

    def tried() -> str:
        try:
            dbschema.connect(p).close()
            return ""
        except dbschema.SchemaError as e:
            return str(e)

    dbschema.connect(p).close()
    real = paths.live_homes, service.plist_path
    paths.live_homes = lambda: [live.resolve()]
    service.plist_path = lambda: plist
    try:
        for label, program, want in [
                ("the checkout it runs", paths.repo_root() / "bin" / "hl", True),
                ("a second clone", home / "clone" / "bin" / "hl", False)]:
            owed()
            plist.write_bytes(plistlib.dumps({"ProgramArguments": [str(program), "daemon"]}))
            msg = tried()
            check(f"service: {label} {'migrates the live home' if want else 'is refused, in one line naming why'}",
                  (not msg) if want else ("installed main" in msg and "\n" not in msg), msg)
            check(f"service: {label}: the database is {'brought up' if want else 'left as it was'}",
                  _versions(p) == list(range(1, dbschema.LATEST_VERSION + (1 if want else 0))))
        owed()
        plist.write_bytes(b"not a plist")
        check("service: a plist that cannot be read refuses", "installed main" in tried())
        plist.unlink()
        check("service: no service installed, nothing to protect: it migrates", not tried())
        owed()
        plist.write_bytes(plistlib.dumps({"ProgramArguments": [str(home / "clone" / "bin" / "hl")]}))
        paths.live_homes = lambda: []
        check("service: a home that is not the live one migrates from any checkout", not tried())
    finally:
        paths.live_homes, service.plist_path = real


def test_two_processes_opening_an_old_database_at_once_migrate_it_once(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "race.db"
    _old_database(p)
    script = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "from pathlib import Path\n"
        "from handline.db import schema\n"
        "while time.time() < float(sys.argv[2]): pass\n"
        "con = schema.connect(Path(sys.argv[1]), migrating=True)\n"
        "print(schema.schema_version(con))\n")
    import time
    go = time.time() + 1.5
    procs = [subprocess.Popen([sys.executable, "-c", script, str(p), str(go)], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for _ in range(6)]
    outs = [(pr.communicate(), pr.returncode) for pr in procs]
    check("race: every process connected", all(rc == 0 for _, rc in outs), str([o[0][1] for o in outs]))
    check("race: and saw the last version", all(o[0][0].strip() == str(dbschema.LATEST_VERSION) for o in outs), str(outs))
    check("race: each migration is recorded once", _versions(p) == list(range(1, dbschema.LATEST_VERSION + 1)))
    con = sqlite3.connect(p)
    check("race: the data is intact", con.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1)
    check("race: and the database is whole", con.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_new_database_is_stamped_and_a_second_connect_changes_nothing(Path(td) / "v1"))
        run(lambda: test_a_plain_connect_runs_the_additive_steps_and_stops_at_one_that_is_not(Path(td) / "v2"))
        run(lambda: test_a_database_one_additive_step_behind_is_brought_up_by_a_plain_connect(Path(td) / "v2a"))
        run(lambda: test_a_database_made_before_the_extra_worktrees_column_gets_it_on_connect(Path(td) / "v2e"))
        run(lambda: test_the_command_a_refusal_names_parses(Path(td) / "v2b"))
        run(lambda: test_the_daemon_migrates_a_database_waiting_for_a_step_once(Path(td) / "v2c"))
        run(lambda: test_a_migrator_that_holds_the_lock_too_long_is_named(Path(td) / "v2d"))
        run(lambda: test_a_newer_database_is_refused_by_connect_and_by_migrate(Path(td) / "v3"))
        run(lambda: test_a_database_an_additive_step_ahead_is_used_as_it_is(Path(td) / "v3a"))
        run(lambda: test_a_database_a_step_ahead_that_is_not_additive_is_refused(Path(td) / "v3b"))
        run(lambda: test_the_additive_mark_is_stamped_and_an_older_stamp_is_filled_in(Path(td) / "v3c"))
        run(lambda: test_only_the_checkout_the_service_runs_may_migrate_the_live_home(Path(td) / "v3d"))
        run(lambda: test_two_processes_opening_an_old_database_at_once_migrate_it_once(Path(td) / "v4"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
