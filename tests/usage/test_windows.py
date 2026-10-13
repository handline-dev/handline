"""Tests: usage windows are found by length, written as a list, and old records still read
(step 7; handline/usage/windows.py, status.read, usage_api.record, db usage_samples).

Run on its own: python3 tests/usage/test_windows.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.vendors.base import Reading  # noqa: E402

NOW = 1_800_000_000

OLD_RECORD = {"pct_5h": 40, "reset_5h": NOW + 1800, "rl_at": NOW - 30,
              "pct_7d": 60, "reset_7d": NOW + 86400, "rl7_at": NOW - 30,
              "collected_at": NOW - 30}


def test_a_record_is_read_by_window_length_in_either_shape(home: Path) -> None:
    use_home(home)
    new = {"collected_at": NOW - 30, "windows": [
        windows.entry(10080, 60, NOW + 86400, NOW - 30),
        windows.entry(300, 40, NOW + 1800, NOW - 30)]}
    for name, raw in (("old names", OLD_RECORD), ("windows list", new)):
        (paths.status_file("alpha").parent).mkdir(parents=True, exist_ok=True)
        paths.status_file("alpha").write_text(json.dumps(raw))
        st = status.read("alpha")
        st.as_of = NOW
        check(f"windows: {name}: the shortest is 300 minutes, 40%, resetting when it says",
              (st.short.minutes, st.short.pct, st.short.reset, st.short.at)
              == (300, 40, NOW + 1800, NOW - 30), str(st.short))
        check(f"windows: {name}: the longest is 10080 minutes, 60%",
              (st.long.minutes, st.long.pct, st.long.reset) == (10080, 60, NOW + 86400),
              str(st.long))
        check(f"windows: {name}: labelled by length, not by name",
              (st.short.label, st.long.label) == ("5h", "7d"))
        check(f"windows: {name}: what may be acted on is the same",
              (st.short_trusted, st.long_trusted, st.short_source, st.long_source)
              == (40, 60, "live", "live"), str((st.short_trusted, st.long_trusted)))


def test_an_old_record_keeps_its_unreadable_and_lost_marks(home: Path) -> None:
    use_home(home)
    paths.status_file("alpha").parent.mkdir(parents=True, exist_ok=True)
    # No pct_5h key at all: nobody wrote the shortest window. Not a quiet one.
    paths.status_file("alpha").write_text(json.dumps(
        {"collected_at": NOW, "pct_7d": 10, "reset_7d": NOW + 999, "rl7_at": NOW}))
    st = status.read("alpha")
    st.as_of = NOW
    check("windows: an old record with no shortest figure is unreadable, not quiet",
          st.short.readable is False and st.short_source == "unknown"
          and st.short_trusted is None, str(st.short))
    paths.status_file("alpha").write_text(json.dumps(
        {"collected_at": NOW, "pct_5h": None, "reset_5h": None, "rl_lost": True,
         "pct_7d": 10, "reset_7d": NOW + 999, "rl7_at": NOW}))
    st = status.read("alpha")
    st.as_of = NOW
    check("windows: a lost mark on the old record is kept",
          st.short.lost and st.short_source == "unknown", str(st.short))
    paths.status_file("alpha").write_text(json.dumps(
        {"collected_at": NOW, "windows": [windows.entry(300, None, None, None)]}))
    st = status.read("alpha")
    st.as_of = NOW
    check("windows: a null figure in the list is a quiet window, and a lone window has no longest",
          st.short_source == "quiet" and st.long.minutes == 0 and st.long_trusted is None,
          str((st.short_source, st.long)))
    paths.status_file("alpha").write_text(json.dumps({"collected_at": NOW, "windows": []}))
    st = status.read("alpha")
    check("windows: a record with no windows at all fails closed",
          st.short_source == "unknown" and not st.assignable()[0], str(st.short))


def test_a_record_is_written_as_windows_only_and_nothing_old_is_removed(home: Path) -> None:
    use_home(home)
    accounts.register("alpha")
    p = paths.status_file("alpha")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({**OLD_RECORD, "pct_5h": 80, "session_id": "sess-1"}))
    ok = usage_api.record(reading_of("alpha", 70, NOW + 1800, 61, NOW + 86400, NOW), path=p)
    got = json.loads(p.read_text())
    check("windows: record writes", ok)
    e = {w["minutes"]: w for w in got["windows"]}
    check("windows: the new shape carries every window the vendor reported",
          sorted(e) == [300, 10080] and e[10080]["used_percent"] == 61, json.dumps(got))
    check("windows: a fresh reading replaces the old record's higher figure in the same window",
          e[300]["used_percent"] == 70, json.dumps(e[300]))
    check("windows: a new write puts no old key on a record that had none",
          "rl_source" in got and got["session_id"] == "sess-1", json.dumps(got))
    check("windows: what an old record held is left on disk, not deleted",
          got["pct_5h"] == 80 and got["reset_7d"] == OLD_RECORD["reset_7d"], json.dumps(got))
    fresh = paths.status_file("beta")
    fresh.parent.mkdir(parents=True, exist_ok=True)
    usage_api.record(reading_of("beta", 5, None, 6, None, NOW), path=fresh)
    check("windows: a record with no history is windows only - no dual write",
          not any(k in json.loads(fresh.read_text()) for k in
                  ("pct_5h", "pct_7d", "reset_5h", "reset_7d", "rl_at", "rl7_at")),
          fresh.read_text())


def test_a_vendor_reports_as_many_windows_as_it_has(home: Path) -> None:
    use_home(home)
    one = Reading("solo", (Window(60, 30, NOW + 600),), NOW)
    three = Reading("trio", (Window(10080, 5, None), Window(60, 1, None), Window(300, 2, None)), NOW)
    check("windows: a reading finds its shortest and longest by length",
          (three.short.minutes, three.long.minutes) == (60, 10080))
    check("windows: a reading of one window has a shortest and no longest",
          one.short.minutes == 60 and one.long is None)
    for r in (one, three):
        accounts.register(r.account)
        p = paths.status_file(r.account)
        p.parent.mkdir(parents=True, exist_ok=True)
        usage_api.record(r, path=p)
    st = status.read("solo")
    check("windows: one window is the shortest and there is no longest",
          (st.short.minutes, st.short.label, st.long.minutes) == (60, "1h", 0),
          str((st.short, st.long)))
    st = status.read("trio")
    check("windows: three windows: the shortest and the longest are the gates",
          (st.short.minutes, st.long.minutes) == (60, 10080), str((st.short, st.long)))
    check("windows: a label says the length",
          [windows.label(m) for m in (300, 10080, 60, 90, 0)] == ["5h", "7d", "1h", "90m", "?"])


def test_an_old_usage_row_reads_and_the_column_is_added_without_losing_it(home: Path) -> None:
    use_home(home)
    con = db.connect()
    try:
        con.execute(
            "INSERT INTO usage_samples(account, ts, exists_, pct_5h, pct_5h_readable, reset_5h, "
            "rl_at, rl_carried, rl_lost, pct_7d, reset_7d, rl7_at, rl7_carried, rl7_lost, "
            "collected_at) VALUES('alpha', ?, 1, 40, 1, ?, ?, 0, 0, 60, ?, ?, 1, 0, ?)",
            (NOW, NOW + 1800, NOW - 30, NOW + 86400, NOW - 30, NOW - 30))
        row = db.usage_samples(con)[0]
        st = replaymod.status_from_row(row)
        check("windows: an old row reconstructs the AccountStatus it described",
              (st.short.pct, st.short.at, st.long.pct, st.long.carried, st.long.reset)
              == (40, NOW - 30, 60, True, NOW + 86400), str((st.short, st.long)))
        db.record_usage_sample(con, "alpha", st, ts=NOW + 60)
        new = db.usage_samples(con)[1]
        back = replaymod.status_from_row(new)
        check("windows: a row written now round-trips through the list alone",
              new["pct_5h"] is None and (back.short.pct, back.long.carried) == (40, True)
              and [e["minutes"] for e in windows.entries(dict(new))] == [300, 10080],
              dict(new))
        con.execute("ALTER TABLE usage_samples DROP COLUMN windows")
        con.commit()
        check("windows: the table can be one that predates the column",
              "windows" not in {r[1] for r in con.execute("PRAGMA table_info(usage_samples)")})
    finally:
        con.close()
    con = support.upgrade()
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(usage_samples)")}
        rows = db.usage_samples(con)
        check("windows: connecting adds the column and keeps the rows that were there",
              "windows" in cols and len(rows) == 2 and rows[0]["pct_5h"] == 40, str(cols))
    finally:
        con.close()


def test_the_ratio_between_windows_is_measured_from_either_kind_of_row(home: Path) -> None:
    use_home(home)
    con = db.connect()
    try:
        def old(ts, p5, p7):
            con.execute(
                "INSERT INTO usage_samples(account, ts, exists_, pct_5h, reset_5h, rl_at, "
                "pct_7d, reset_7d, rl7_at, collected_at) VALUES('a', ?, 1, ?, ?, ?, ?, ?, ?, ?)",
                (ts, p5, NOW + 3600, ts, p7, NOW + 86400, ts, ts))
        old(NOW, 10, 20)
        old(NOW + 60, 30, 32)
        st = status_of("a", exists=True, pct_5h=40, reset_5h=NOW + 3600, rl_at=NOW + 120,
                       pct_7d=42, reset_7d=NOW + 86400, rl7_at=NOW + 120, collected_at=NOW + 120)
        db.record_usage_sample(con, "a", st, ts=NOW + 120)
        got = burnrate.long_ratios(db.usage_samples(con))
        check("windows: pairs of old rows and of new rows both count", abs(got.get("a", 0) - 22 / 30) < 1e-9,
              str(got))
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_record_is_read_by_window_length_in_either_shape(Path(td) / 'w1'))
        run(lambda: test_an_old_record_keeps_its_unreadable_and_lost_marks(Path(td) / 'w2'))
        run(lambda: test_a_record_is_written_as_windows_only_and_nothing_old_is_removed(Path(td) / 'w3'))
        run(lambda: test_a_vendor_reports_as_many_windows_as_it_has(Path(td) / 'w4'))
        run(lambda: test_an_old_usage_row_reads_and_the_column_is_added_without_losing_it(Path(td) / 'w5'))
        run(lambda: test_the_ratio_between_windows_is_measured_from_either_kind_of_row(Path(td) / 'w6'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
