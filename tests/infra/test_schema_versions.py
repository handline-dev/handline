"""Tests: the schema is versioned. An old database migrates once, however many processes open it; a
newer one is refused; one the current code made is accepted untouched; `connect` migrates nothing that
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
    """The real migrations with `extra` steps after them; returns what to put back."""
    real = dbschema.MIGRATIONS, dbschema.LATEST_VERSION
    dbschema.MIGRATIONS = list(dbschema.MIGRATIONS) + [(real[1] + 1 + i, f"fake{i}", fn) for i, fn in enumerate(extra)]
    dbschema.LATEST_VERSION = dbschema.MIGRATIONS[-1][0]
    return real


def _restore(real) -> None:
    dbschema.MIGRATIONS, dbschema.LATEST_VERSION = real


def test_a_plain_connect_gives_an_unstamped_database_the_baseline_and_nothing_more(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "old.db"
    _old_database(p)
    check("old: it starts with no versions", not _has_table(p))
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
    check("old: connect ran the baseline, stamped version 1 and no step above it", _versions(p) == [1] and not ran, str(_versions(p)))
    check("old: and its data is still there", sqlite3.connect(p).execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1)
    check("old: a step above the baseline is refused, naming a command that exists",
          "older" in refused and f"`{dbschema.RESTART_COMMAND}`" in refused, refused)


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
        run(lambda: test_a_plain_connect_gives_an_unstamped_database_the_baseline_and_nothing_more(Path(td) / "v2"))
        run(lambda: test_the_command_a_refusal_names_parses(Path(td) / "v2b"))
        run(lambda: test_the_daemon_migrates_a_database_waiting_for_a_step_once(Path(td) / "v2c"))
        run(lambda: test_a_migrator_that_holds_the_lock_too_long_is_named(Path(td) / "v2d"))
        run(lambda: test_a_newer_database_is_refused_by_connect_and_by_migrate(Path(td) / "v3"))
        run(lambda: test_two_processes_opening_an_old_database_at_once_migrate_it_once(Path(td) / "v4"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
