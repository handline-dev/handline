"""Tests: the commands that cannot be undone (`ocp account remove`, `ocp doctor --drop-dead-columns`) each
have a --dry-run that prints what they would do and the newest backup, and refuse to run without a backup
from the last five minutes. --yes skips the question and never the gate.

Run on its own: tools/test.sh tests/infra/test_irreversible_gate.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.cli import health  # noqa: E402
from handline.db import schema as dbschema  # noqa: E402

WINDOW = backupmod.GATE_MAX_AGE_S


def _cli(*argv):
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = climod.main(list(argv))
    return rc, buf.getvalue(), err.getvalue()


def test_a_backup_counts_for_five_minutes_whether_taken_or_checked_unchanged(home: Path) -> None:
    use_home(home)
    now = time.time()
    check("gate: with no backup at all it refuses and names the command that makes one",
          "there is no backup yet" in (backupmod.require_recent() or "") and "`ocp backup`" in backupmod.require_recent())
    check("gate: the window is five minutes", WINDOW == 300 and "5 minutes" in backupmod.require_recent())
    fresh_backup(age_s=60)
    check("gate: a backup a minute old passes", backupmod.require_recent() is None)
    fresh_backup(age_s=WINDOW - 5)
    check("gate: and one just inside the window", backupmod.require_recent() is None)
    p = fresh_backup(age_s=WINDOW + 60)
    why = backupmod.require_recent()
    check("gate: one just outside refuses, with the snapshot's name, its age and the command",
          why and p.name in why and "was taken 6m ago" in why and "`ocp backup`" in why, str(why))
    # `ocp backup` that finds nothing changed records that the newest snapshot is still the state.
    (backupmod.backup_dir() / backupmod.FINGERPRINT).write_text("x")
    check("gate: a snapshot checked unchanged just now still counts, however old it is",
          backupmod.require_recent() is None)
    check("gate: and its description says so", "checked unchanged" in backupmod.describe_newest(), backupmod.describe_newest())
    check("gate: and the description names the path and the time",
          str(p) in backupmod.describe_newest() and "ago" in backupmod.describe_newest())
    check("gate: with no snapshot the description says none", (p.unlink() or True)
          and "none in" in backupmod.describe_newest())


def test_a_real_backup_command_that_finds_nothing_changed_keeps_the_gate_open(home: Path) -> None:
    """The gate names `ocp backup` as the fix; where the state has not changed since the newest snapshot,
    that command writes nothing, and must still leave a backup that passes."""
    use_home(home)
    (home / "team.yaml").write_text("teams: {}\n")
    first = backupmod.snapshot(home, now=time.time() - 3600)
    check("backup: the first snapshot is written", first is not None and first.exists())
    old = time.time() - 3600
    import os
    os.utime(first, (old, old))
    os.utime(backupmod.backup_dir(home) / backupmod.FINGERPRINT, (old, old))
    check("backup: an hour on, the gate refuses", backupmod.require_recent(home) is not None)
    again = backupmod.snapshot(home)
    check("backup: running it writes nothing (the state is the same) and the gate opens",
          again is None and backupmod.require_recent(home) is None)


def _account_world(home: Path) -> None:
    _daemon_home(home, 20)
    for a in ("alpha", "beta"):
        accounts.register(a)
    accounts.logged_in = lambda a: False


def test_account_remove_dry_run_says_what_it_would_do_and_changes_nothing(home: Path) -> None:
    _account_world(home)
    fresh_backup(age_s=30)
    con = db.connect()
    try:
        con.execute("UPDATE agent_state SET account = 'beta' WHERE agent = 'backend'")
        db.set_closed(con, "backend", "sess-1")
        con.commit()
    finally:
        con.close()
    rc, out, err = _cli("account", "remove", "beta", "--dry-run")
    check("dry run: it says what it would do and exits 0 when the real run would go ahead",
          rc == 0 and "would remove account 'beta'" in out, out + err)
    check("dry run: the directory it moves and where", str(home / "accounts" / "beta") in out
          and ".retired" in out and "kept, not deleted" in out, out)
    check("dry run: who is forgotten on it", "backend" in out and "start fresh" in out, out)
    check("dry run: the newest backup's path and time, and the gate passed",
          "newest backup:" in out and "orcapipe-20260101-000000.tar.gz" in out and "ago" in out
          and "backup gate: passed" in out, out)
    check("dry run: and nothing changed", "beta" in paths.list_accounts()
          and not (home / "accounts" / ".retired").exists())
    fresh_backup(age_s=WINDOW + 600)
    rc, out, err = _cli("account", "remove", "beta", "--dry-run")
    check("dry run: with no recent backup it says the real run would refuse, and exits 1",
          rc == 1 and "would refuse: no backup from the last 5 minutes" in out and "`ocp backup`" in out, out + err)
    con = db.connect()
    try:
        con.execute("UPDATE agent_state SET closed_at = NULL, resume_session = NULL WHERE agent = 'backend'")
        db.set_agent_state(con, "backend", "RUNNING")
        con.commit()
    finally:
        con.close()
    rc, out, err = _cli("account", "remove", "beta", "--dry-run")
    check("dry run: an account with a session on it is refused for that, before the backup is mentioned",
          rc == 1 and out.startswith("would refuse: account 'beta' has a session open") and "backup" not in out, out)
    rc, out, err = _cli("account", "remove", "nobody", "--dry-run")
    check("dry run: an unknown account is said to be unknown", rc == 1 and "no account 'nobody'" in out, out)


def test_account_remove_refuses_without_a_recent_backup_even_with_yes(home: Path) -> None:
    _account_world(home)
    fresh_backup(age_s=WINDOW + 600)
    rc, out, err = _cli("account", "remove", "beta", "--yes")
    check("remove: --yes does not skip the backup gate: refused, naming the command",
          rc == 1 and "not removing 'beta'" in err and "`ocp backup`" in err and "beta" in paths.list_accounts(), out + err)
    check("remove: and it asked nothing and moved nothing", not (home / "accounts" / ".retired").exists())
    asked: list[bool] = []
    ok, msg = accounts.remove_account("beta", lambda: (asked.append(True), True)[1], lambda a, e: 0)
    check("remove: the question is not even asked while there is no backup", not ok and asked == [], msg)
    fresh_backup(age_s=10)
    rc, out, err = _cli("account", "remove", "beta", "--yes")
    check("remove: with a backup from the last minutes --yes goes through",
          rc == 0 and "beta" not in paths.list_accounts(), out + err)


def _dead_columns(home: Path) -> list[str]:
    _daemon_home(home, 20)
    con = db.connect()
    try:
        for col in dbschema._LEGACY_REQUEST_COLUMNS:
            if col not in dbschema._cols(con, "requests"):
                con.execute(f"ALTER TABLE requests ADD COLUMN {col} INTEGER")
        return dbschema.unread_request_columns(con)
    finally:
        con.close()


def _doctor(**kw):
    args = argparse.Namespace(settle_leftovers=False, drop_dead_columns=True, dry_run=False, no_color=True, **kw)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = health.cmd_doctor(args)
    return rc, out.getvalue(), err.getvalue()


def test_drop_dead_columns_dry_run_names_the_columns_and_the_backup_and_drops_nothing(home: Path) -> None:
    dead = _dead_columns(home)
    fresh_backup(age_s=20)
    real = health.stale_long_lived
    health.stale_long_lived = lambda started_at=None: []
    try:
        args = argparse.Namespace(settle_leftovers=False, drop_dead_columns=True, dry_run=True, no_color=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = health.cmd_doctor(args)
        out = buf.getvalue()
        con = db.connect()
        try:
            left = dbschema._cols(con, "requests")
        finally:
            con.close()
        check("dry run: every dead column is named, and the one read-first step before dropping",
              rc == 0 and all(c in out for c in dead) and "would drop from the requests table" in out
              and "first moving" in out, out)
        check("dry run: the newest backup's path and time, and the all-clear", "newest backup:" in out
              and "orcapipe-20260101-000000.tar.gz" in out and "passed" in out, out)
        check("dry run: and none was dropped, and no doctor checks ran", set(dead) <= left and "checks," not in out)
    finally:
        health.stale_long_lived = real


def test_drop_dead_columns_refuses_for_a_stale_process_or_no_backup_and_keeps_the_columns(home: Path) -> None:
    dead = _dead_columns(home)
    real = health.stale_long_lived
    try:
        fresh_backup(age_s=WINDOW + 600)
        health.stale_long_lived = lambda started_at=None: []
        rc, out, err = _doctor()
        con = db.connect()
        try:
            left = dbschema._cols(con, "requests")
        finally:
            con.close()
        check("drop: with no backup from the last 5 minutes it refuses, names `ocp backup`, drops nothing",
              rc == 1 and "not dropping:" in err and "`ocp backup`" in err and set(dead) <= left, err)
        health.stale_long_lived = lambda started_at=None: ["bridge (pid 4242)"]
        fresh_backup(age_s=5)
        rc, out, err = _doctor()
        check("drop: a stale long-lived process still refuses first, whatever the backup",
              rc == 1 and "bridge (pid 4242)" in err, err)
        args = argparse.Namespace(settle_leftovers=False, drop_dead_columns=True, dry_run=True, no_color=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = health.cmd_doctor(args)
        check("drop: its dry run says so, and exits 1, listing the process and a passing backup gate",
              rc == 1 and "would refuse: bridge (pid 4242)" in buf.getvalue()
              and "would refuse: no backup" not in buf.getvalue(), buf.getvalue())
        fresh_backup(age_s=WINDOW + 600)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = health.cmd_doctor(args)
        check("drop: and with both wrong, both are listed",
              "would refuse: bridge" in buf.getvalue() and "would refuse: no backup" in buf.getvalue(), buf.getvalue())
        health.stale_long_lived = lambda started_at=None: []
        fresh_backup(age_s=5)
        rc, out, err = _doctor()
        con = db.connect()
        try:
            left = dbschema._cols(con, "requests")
        finally:
            con.close()
        check("drop: with a recent backup and nothing stale the real run drops them",
              rc in (0, 1) and "dropped:" in out and not set(dead) & left, out + err)
    finally:
        health.stale_long_lived = real
    args = argparse.Namespace(settle_leftovers=False, drop_dead_columns=False, dry_run=True, no_color=True)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = health.cmd_doctor(args)
    check("drop: --dry-run alone is refused, not read as a plain doctor run",
          rc == 2 and "--dry-run is for --drop-dead-columns" in err.getvalue())


def main() -> int:
    def tests(td):
        run(lambda: test_a_backup_counts_for_five_minutes_whether_taken_or_checked_unchanged(Path(td) / "g1"))
        run(lambda: test_a_real_backup_command_that_finds_nothing_changed_keeps_the_gate_open(Path(td) / "g2"))
        run(lambda: test_account_remove_dry_run_says_what_it_would_do_and_changes_nothing(Path(td) / "g3"))
        run(lambda: test_account_remove_refuses_without_a_recent_backup_even_with_yes(Path(td) / "g4"))
        run(lambda: test_drop_dead_columns_dry_run_names_the_columns_and_the_backup_and_drops_nothing(Path(td) / "g5"))
        run(lambda: test_drop_dead_columns_refuses_for_a_stale_process_or_no_backup_and_keeps_the_columns(Path(td) / "g6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
