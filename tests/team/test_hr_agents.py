"""Tests: agents, teams, proposals, vendors, launch, trust and doctor.

Run on its own: python3 tests/team/test_hr_agents.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403


# ---------------------------------------------------------------- yaml
def test_yaml() -> None:
    text = (
        "name: backend\naccount: alpha\nmodel: sonnet\nenabled: true\n"
        "peers:\n  - name: reviewer\n    relation: review\n  - name: archivist\n"
        "limits:\n  ctx_pct: 75\n  note: \"a: colon\"\n  none: null\n"
    )
    got = yamlcompat.parse(text)
    check("yaml: scalars", got["name"] == "backend" and got["enabled"] is True)
    check("yaml: nested map", got["limits"]["ctx_pct"] == 75)
    check("yaml: quoted value with colon", got["limits"]["note"] == "a: colon")
    check("yaml: null", got["limits"]["none"] is None)
    check("yaml: list of maps", [p["name"] for p in got["peers"]] == ["reviewer", "archivist"])
    try:
        yamlcompat.parse("a: &anchor x\n")
        check("yaml: rejects unsupported syntax", False, "no error raised")
    except yamlcompat.ConfigError:
        check("yaml: rejects unsupported syntax", True)


# ---------------------------------------------------------------- agents
def test_agents(home: Path) -> None:
    os.environ["OCP_HOME"] = str(home)
    agents.register("backend", model="sonnet")
    check("agent registers with no account", "account" not in agents.read("backend"))
    agents.register("backend")
    cfg = agents.read("backend")
    check("re-register preserves other fields", cfg.get("model") == "sonnet", str(cfg))
    d = home / "agents" / "backend"
    check("agent dirs created", all((d / s).is_dir() for s in ("inbox", "journal")))
    (d / "base.md").write_text("x" * 5000)
    over = [fn for fn, _, _, o in agents.budget_report("backend") if o]
    check("base.md budget is enforced", "base.md" in over)


def test_team_routing() -> None:
    t = team.Team(yamlcompat.parse(DESIGN_TEAM))
    check("route: wildcard edge to reviewer", t.route("backend", "reviewer").allowed)
    check("route: reviewer may answer anyone", t.route("reviewer", "backend").allowed)
    check("route: product may reach eng team", t.route("product", "backend").allowed)
    check("route: analytics may reach product", t.route("analytics", "product").allowed)

    d = t.route("backend", "marketing")
    check("route: cross-team deny fires", not d.allowed and "product" in d.reason, d.reason)
    d = t.route("marketing", "backend")
    check("route: deny is symmetric", not d.allowed, d.reason)

    d = t.route("backend", "frontend")
    check("route: unlisted edge denied by default", not d.allowed, d.reason)
    check("route: refusal explains the default", "denied by default" in d.reason)

    check("route: self-ask denied", not t.route("backend", "backend").allowed)
    check("route: unknown target denied", not t.route("backend", "nobody").allowed)

    d = t.route("backend", "human")
    check("route: escalation target is routable", d.allowed and d.creates_gate, d.reason)
    check("route: escalation kind preserved", d.kind == "escalate")

    check("route: fire_and_forget preserved",
          t.route("backend", "archivist").response == "fire_and_forget")
    check("route: chain depth limit", not t.route("backend", "reviewer", chain_depth=4).allowed)

    # A deny must win even though an earlier wildcard edge would match.
    t2 = team.Team(yamlcompat.parse(
        "agents:\n  a: {}\n  b: {}\n"
        'edges:\n  - { from: "*", to: "*", relation: peer }\n'
        'deny:\n  - { from: "a", to: "b", reason: "no" }\n'
    ))
    check("route: deny beats a matching wildcard edge", not t2.route("a", "b").allowed)
    check("route: wildcard still allows others", t2.route("b", "a").allowed)


def test_yaml_differential() -> None:
    """The fallback must never disagree with PyYAML silently.

    Two parsers reading the same routing policy differently is worse than
    having no fallback: `creates_gate: no` was True under one and False under
    the other, which decides whether an escalation needs a human.

    Without PyYAML on the interpreter running the suite the comparison runs
    under one that has it, and with none anywhere the test fails: a skip was
    how it went a day unexercised on the default python and then, when its
    cases were deleted, unnoticed.
    """
    try:
        import yaml  # noqa: F401
    except ImportError:
        py = _python_with_pyyaml()
        if py is None:
            check("yaml differential: a python3 with PyYAML to compare against", False,
                  "none found; pip install pyyaml, or use /usr/bin/python3")
            return
        r = subprocess.run(
            [py, "-c", "import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]; "
             "import test_ocp as t; t._check_yaml_differential(); "
             "sys.exit(1 if t.FAIL else 0)",
             str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent)],
            capture_output=True, text=True, env={k: v for k, v in os.environ.items()
                                                 if k != "OCP_AGENT"})
        check(f"yaml differential: under {py}, no silent disagreement with PyYAML",
              r.returncode == 0 and "PASS  yaml differential" in r.stdout,
              (r.stdout + r.stderr)[-600:])
        return
    _check_yaml_differential()


def test_the_bundled_parser_reads_what_pyyaml_reads_as_strings_and_refuses_the_rest() -> None:
    """Under any interpreter: what PyYAML reads as a non-string is refused, not
    returned as text, and what it reads as a string is one here."""
    for src, label in [
        ("d: 2026-09-20\n", "a date"), ("d: 2026-09-20 10:00:00\n", "a datetime"),
        ("d: 2026-9-2t1:02:03.5 +09:00\n", "a datetime with a zone"),
        ("n: +.inf\n", "+.inf"), ("n: -.inf\n", "-.inf"), ("n: .INF\n", ".INF"),
        ("n: .nan\n", ".nan"), ("n: 1_000\n", "an underscored int"),
        ("n: -1_000.5\n", "a signed underscored float"), ("n: +1_0.5e+3\n", "a signed exponent"),
        ("n: 0x_1F\n", "an underscored hex"), ("n: 0b1_0\n", "an underscored binary"),
        ('q: "a\\nb"\n', "an escape other than \\\\ and \\\""),
    ]:
        try:
            got = yamlcompat.parse(src)
            check(f"yaml: refuses {label}", False, f"parsed as {got!r}")
        except yamlcompat.ConfigError:
            check(f"yaml: refuses {label}", True)
    got = _parsed("a: y\nb: n\nc: yEs\nd: tRUE\ne: nULL\nf: -.5\ng: 08\nh: 1e3\n")
    check("yaml: y, n, mixed case, a signed .5, 08 and 1e3 are strings, as in PyYAML",
          got == {"a": "y", "b": "n", "c": "yEs", "d": "tRUE", "e": "nULL", "f": "-.5",
                  "g": "08", "h": "1e3"}, str(got))
    check("yaml: the spellings PyYAML takes are still booleans and null",
          _parsed("a: Yes\nb: OFF\nc: True\nd: ~\ne: NULL\n")
          == {"a": True, "b": False, "c": True, "d": None, "e": None})
    check("yaml: a doubled single quote, and the two escapes, read as one character",
          _parsed("a: 'it''s'\nb: \"x\\\"y\"\nc: \"p\\\\q\"\n")
          == {"a": "it's", "b": 'x"y', "c": "p\\q"})
    unread = [(v, _roundtrip(v)) for v in DUMP_STRINGS if _roundtrip(v) != {"k": v}]
    check("yaml: everything dump writes is read back as the same string", not unread,
          str(unread[:3]))
    check("yaml: every non-string dump writes is read back as the same value",
          all(repr(_roundtrip(v)) == repr({"k": v}) for v in DUMP_VALUES))
    check("yaml: a why with an arrow is written quoted and read back",
          _roundtrip("handoff -> resolve, then merge", "why")
          == {"why": "handoff -> resolve, then merge"})
    # _outside_quotes decides what _check may refuse, and breaking it only makes
    # _check refuse more, which the differential tolerates - so these two say
    # what must be read, not merely not misread.
    check("yaml: an indicator inside single quotes is text",
          _parsed("a: 'a & b'\n") == {"a": "a & b"}, str(_parsed("a: 'a & b'\n")))
    check("yaml: an escaped quote does not end the quoted text",
          _parsed('a: "x\\" & y"\n') == {"a": 'x" & y'}, str(_parsed('a: "x\\" & y"\n')))
    check("yaml: a quoted key is its text, whatever it looks like",
          _parsed("'1': a\n\"on\": b\n'2026-09-20': c\n") == {"1": "a", "on": "b", "2026-09-20": "c"})
    check("yaml: a plain key that is text to PyYAML too is read unchanged",
          _parsed("y: 1\n1e3: 2\n08: 3\na b: 4\n-: 5\n")
          == {"y": 1, "1e3": 2, "08": 3, "a b": 4, "-": 5})
    check("yaml: a plain key PyYAML reads as a number, bool, null or date is refused",
          all(_refused(f"{k}: x\n") for k in ("1", "on", "Yes", "true", "null", "~", "1.5", "0x10",
                                             "1_000", "2026-09-20", "+.inf", "12:30", "")),
          str([k for k in ("1", "on", "null", "~", "") if not _refused(f"{k}: x\n")]))
    check("yaml: in a list item, a flow map and a nested map too",
          all(_refused(src) for src in ("- on: a\n", "{a: 1, 1: x}\n", "x:\n  1: a\n",
                                        "x:\n  - { true: a }\n")))
    check("yaml: text after a quoted key's closing quote is refused, not read as the key",
          all(_refused(src) for src in ('"a" b: 1\n', "'a' b: 1\n", '{ "a" b: 1 }\n')))
    check("yaml: a key that only contains an indicator is text, as in PyYAML",
          _parsed("a>b: 1\nx*y: 2\nq&r: 3\n") == {"a>b": 1, "x*y": 2, "q&r": 3})
    try:
        yamlcompat.parse(">x: 1\n")
        why_key = ""
    except yamlcompat.ConfigError as e:
        why_key = str(e)
    check("yaml: a key that starts with an indicator says what it is and how to fix it",
          "key '>x'" in why_key and "quote it" in why_key and "has PyYAML" not in why_key, why_key)
    check("yaml: an anchor or an alias where a key starts is refused",
          _refused("&a k: v\n") and _refused("*a: v\n"))
    check("yaml: a quote opened and never closed is refused, not read with its quote",
          all(_refused(src) for src in ('a: "x & y\n', "a: 'x & y\n", 'a: "x" y\n',
                                        "a: 'x' 'y'\n", 'a: "x\\"\n', 'a: "\n')))
    check("yaml: bare, the same arrow is still refused, not read as anything else",
          _refused("why: handoff -> resolve\n"))
    for src, label in [("n: +12:30\n", "a signed sexagesimal"), ("n: 1_0:30\n", "an underscored one"),
                       ("n: +12:30.5\n", "a signed fractional one")]:
        check(f"yaml: refuses {label}", _refused(src))
    check("yaml: .iNf and -.nan are strings, as in PyYAML",
          _parsed("a: .iNf\nb: -.nan\n") == {"a": ".iNf", "b": "-.nan"})
    for value in (float("inf"), float("-inf"), float("nan")):
        try:
            yamlcompat.dump({"k": value})
            check(f"yaml: dump refuses {value!r}, which no parser reads back", False)
        except yamlcompat.ConfigError:
            check(f"yaml: dump refuses {value!r}, which no parser reads back", True)
    try:
        yamlcompat.dump({"k": "a\nb"})
        check("yaml: dump refuses a control character rather than write a broken line", False)
    except yamlcompat.ConfigError:
        check("yaml: dump refuses a control character rather than write a broken line", True)


def test_yaml_rejects_ambiguity() -> None:
    for src, label in [
        ("a: &anchor x\n", "anchors"),
        ("a: 12:30\n", "sexagesimals"),
        ("n: inf\n", "inf"),
        ("n: nan\n", "nan"),
        ("a:\n\tb: 1\n", "tab indentation"),
        ("a: 1\n---\nb: 2\n", "multiple documents"),
        ("d:\n  - { a: 1\n", "unbalanced flow collection"),
        ("d: }broken\n", "stray closing brace"),
    ]:
        try:
            yamlcompat.parse(src)
            check(f"yaml: rejects {label}", False, "parsed instead of raising")
        except yamlcompat.ConfigError:
            check(f"yaml: rejects {label}", True)


def test_a_poisoned_proposal_field_is_refused_at_the_write_not_read_back_on_the_founders_screen(
        home: Path) -> None:
    """The real path: a CSI byte in --repo, through hr's own save/load/
    escalation. Before the fix this wrote, reloaded, and reached the text
    the founder's screen draws - reproduced end to end under the bundled
    parser (no PyYAML), which has no printable-character check at all,
    unlike real PyYAML's own Reader (a side effect of PyYAML's spec
    compliance, not a guard ocp controls - the bundled parser is what most
    of the fleet actually runs under)."""
    _daemon_home(home, 20)
    poisoned = "repo\u009b31mEVIL\u009bm"
    prop = {"id": "poisontest", "kind": "add", "agent": "newagent",
            "requested_by": "someone", "why": "test", "created": int(time.time()),
            "account": "alpha", "repo": poisoned}
    try:
        hr.save(prop)
        check("hr: a proposal with a C1 byte in a field is refused at save", False,
              "saved instead of raising")
    except yamlcompat.ConfigError:
        check("hr: a proposal with a C1 byte in a field is refused at save", True)
    check("hr: nothing was written", hr.path_of("poisontest").exists() is False)


def test_agents_read_refuses_a_value_a_hand_edit_wrote_with_a_control_character(
        home: Path) -> None:
    """#1571's ruling: REFUSE on read, not strip. A hand-edited agent.yaml -
    never written by dump(), so never checked before this - reaches
    agents.read unguarded until now.

    A C1 byte (0x9b) is refused either way, but under real PyYAML its own
    Reader (check_printable) refuses it first - a guard ocp does not
    control, and the same reason test_a_poisoned_proposal_field... above
    needs the bundled parser to show its own message at all. A tab inside a
    quoted scalar is different: PyYAML accepts it (measured: yaml.safe_load
    reads 'k: \"a\\tb\"' as the literal tab), so it always reaches
    refuse_unsafe_values, under either parser - that is what the file/key
    assertions below use, so they hold regardless of which python3 runs
    the suite."""
    _daemon_home(home, 20)
    af = agents.agent_file("backend")
    hand_edit_agent("backend", af.read_text().rstrip("\n") + '\nrepo: "before\u009bafter"\n')
    try:
        agents.read("backend")
        check("agents.read: refuses a C1 byte in repo", False, "read instead of raising")
    except yamlcompat.ConfigError:
        check("agents.read: refuses a C1 byte in repo", True)

    hand_edit_agent("backend", af.read_text().split("\nrepo:")[0] + '\nrepo: "before\tafter"\n')
    try:
        agents.read("backend")
        check("agents.read: refuses a tab in repo (reaches refuse_unsafe_values "
              "under either parser)", False, "read instead of raising")
    except yamlcompat.ConfigError as e:
        check("agents.read: refuses a tab in repo (reaches refuse_unsafe_values "
              "under either parser)", True)
        check("agents.read: names the file", str(af) in str(e), str(e))
        check("agents.read: names the key", "'repo'" in str(e), str(e))

    # Control: a clean agent.yaml still reads.
    agents.register("clean")
    data = agents.read("clean")
    check("agents.read: a clean file still reads", isinstance(data, dict) and data, str(data))


def test_a_name_that_yaml_would_not_read_as_text_is_refused_where_it_is_typed(home: Path) -> None:
    """A name is written unquoted into team.yaml, agent.yaml and config.yaml. `no`
    is False to PyYAML and a refusal to the bundled parser, and a refusal on a
    key makes the whole file unloadable, so the name is turned away when it is
    typed and not when every command next reads the roster."""
    restore_world()
    use_home(home)
    bad = ("no", "on", "off", "yes", "1", "2026-09-20", "null", "~", "true", "0x10", "a: b")
    good = ("harbor", "harbor.deck", "orcapipe.purser", "a-b_c", "y", "1e3", "é")
    check("names: what yaml reads as something else is not plain text",
          not any(yamlcompat.is_plain_text(n) for n in bad), str([n for n in bad if yamlcompat.is_plain_text(n)]))
    check("names: what ocp names its agents is",
          all(yamlcompat.is_plain_text(n) for n in good), str([n for n in good if not yamlcompat.is_plain_text(n)]))
    refused = []
    for n in bad:
        try:
            agents.register(n)
            refused.append(n)
        except ValueError:
            pass
    check("names: agents.register turns each away and creates nothing",
          not refused and not any((paths.agent_dir(n)).exists() for n in bad), str(refused))
    for n in good:
        agents.register(n)
    check("names: and takes each ordinary one", set(good) <= set(paths.list_agents()))
    # auditor #1774: is_plain_text passes a tab, so only register's own
    # unsafe-character check stands between it and the roster.
    try:
        agents.register("a\tb")
        check("names: agents.register turns away a name holding a tab", False, "registered")
    except ValueError as e:
        check("names: agents.register turns away a name holding a tab",
              "bad agent name" in str(e) and not paths.agent_dir("a\tb").exists(), str(e))
    for kind in ("add", "rename"):
        ok, said = hr._draft(kind, "no" if kind == "add" else "harbor", "x", "why",
                             None, None, None, "on" if kind == "rename" else None)
        check(f"names: hr {kind} is refused before anything is raised or written",
              ok == {} and "bad agent name" in said and "choose another" in said, str((ok, said)))
    con = db.connect()
    try:
        before = len(actions.messages(con))
        ok, said = hr.propose("add", "no", requester="harbor", why="w")
        check("names: hr.propose of one queues nothing for the founder",
              not ok and len(actions.messages(con)) == before, said)
    finally:
        con.close()
    text = "agents:\n  harbor: {}\n"
    check("names: the line hr writes for an agent called no would make team.yaml unloadable",
          _refused(text + "  no: {}\n"))
    try:
        hr._add_text(text, "no", None)
        check("names: so the writer refuses to write it", False)
    except yamlcompat.ConfigError:
        check("names: so the writer refuses to write it", True)
    # The team's name is written as a block key too, and the guard has to stand at
    # every door: proposing, and the write itself, which an old proposal reaches.
    hand_edit_team(team.STARTER)
    before = paths.team_file().read_text()
    listed = yamlcompat.parse(team.STARTER)
    mover = next(iter(listed["agents"]))
    ok, said = hr._draft("add", "dev1", "x", "why", "no", None, None, None)
    check("names: hr add of an agent into a team called no is refused at the proposal",
          ok == {} and "bad team name" in said and "choose another" in said, str((ok, said)))
    done, said = hr._in_team_yaml("dev1", "no", add=True)
    check("names: and at the write, leaving team.yaml as it was",
          not done and "bad team name" in said and paths.team_file().read_text() == before, said)
    for label, call in (
            ("a rename into a name yaml reads as a bool",
             lambda: hr._rename_text(team.STARTER, mover, "on", None)),
            ("a rename that joins a team called no",
             lambda: hr._rename_text(team.STARTER, mover, "newname", "no")),
            ("a join of an agent called no", lambda: hr._join_team(team.STARTER, "no", "eng"))):
        try:
            call()
            check(f"names: {label} is refused at the write", False)
        except yamlcompat.ConfigError:
            check(f"names: {label} is refused at the write", True)
    if yamlcompat.HAVE_PYYAML:
        lockout = "teams:\n  no: [dev1]\n"
        check("names: under PyYAML the same line loads, with the key False - which the "
              "bundled parser refuses, so only the guard stands between the two",
              False in yamlcompat.load(lockout)["teams"] and _refused(lockout))
    for n in bad[:6]:
        try:
            accounts.register(n)
            check(f"names: accounts.register turns away {n!r}", False)
        except ValueError:
            check(f"names: accounts.register turns away {n!r}", True)


def test_one_unreadable_proposal_does_not_take_the_hr_listing_down(home: Path) -> None:
    """`hr.listing()` feeds `ocp hr list` and the bridge's waiting list. A file
    that does not parse used to raise out of both, and the parser refuses more
    than it did, so the same one file would have hidden every proposal. It is
    left out and named, and a file that parses to something that is not a
    proposal is treated the same way."""
    restore_world()
    use_home(home)
    d = hr.dir_()
    (d / "good.yaml").write_text("id: good\nkind: add\nagent: newone\nstate: proposed\n"
                                 "requested_by: x\n")
    (d / "broken.yaml").write_text("id: broken\nkind: add\nagent: [unclosed\n")
    (d / "alist.yaml").write_text("- just\n- a list\n")
    (d / "other.yaml").write_text("colour: blue\n")
    (d / "kindless.yaml").write_text("id: k\nagent: a\n")
    (d / "binary.yaml").write_bytes(b"id: \xff\xfe\n")
    skipped: list[str] = []
    rows = hr.listing(skipped)
    check("hr listing: the one good proposal is listed",
          [r["id"] for r in rows] == ["good"], str(rows))
    check("hr listing: each file left out is named, with the reason",
          sorted(x.split(":")[0] for x in skipped)
          == ["alist.yaml", "binary.yaml", "broken.yaml", "kindless.yaml", "other.yaml"]
          and sum("not a proposal" in x for x in skipped) == 3, str(skipped))
    check("hr listing: asked for no report, it still returns the good ones",
          [r["id"] for r in hr.listing()] == ["good"])
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = climod.main(["hr", "list"])
    check("hr list: prints the good proposal and says what it skipped, and succeeds",
          code == 0 and "good" in out.getvalue() and "skipped, cannot be read - broken.yaml" in err.getvalue()
          and "Traceback" not in err.getvalue(), out.getvalue() + err.getvalue())
    con = db.connect()
    try:
        waits = actions.founder_waits(con)
        check("the bridge's waiting list is built with the bad files present",
              isinstance(waits, list))
    finally:
        con.close()
    ok, said = hr.apply("broken")
    check("hr apply: a proposal that cannot be read says so instead of raising",
          not ok and "cannot be read" in said, said)
    ok, said = hr.apply("other")
    check("hr apply: a file that is not a proposal is said not to be one",
          not ok and "not a proposal" in said, said)
    ok, said = hr.apply("binary")
    check("hr apply: one that is not text says so too", not ok and "cannot be read" in said, said)
    for f in d.glob("*.yaml"):
        if f.name != "good.yaml":
            f.unlink()
    skipped.clear()
    check("hr listing: with the bad files gone nothing is reported",
          [r["id"] for r in hr.listing(skipped)] == ["good"] and skipped == [])


def test_a_rename_names_the_agents_orca_worktree(home: Path) -> None:
    """What the founder reads in Orca's sidebar is the worktree's display name,
    so a rename that left it alone looks like a rename that failed. The label is
    set, read back, and never moves anything: no folder, no memory, no terminal."""
    _daemon_home(home, 20)
    hand_edit_team("""
teams:
  dev: [oldname]
agents:
  oldname: {}
  marina:  {}
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
    agents.register("oldname")
    wt = home / "trees" / "oldname"
    wt.mkdir(parents=True)
    con = db.connect()
    real_run, real_avail = orcamod._run, orcamod.available
    fake = FakeOrca({str(wt): "oldname"})
    try:
        sessions_are()
        db.set_terminal(con, "oldname", "term_x", f"repo-id::{wt}")
        orcamod._run, orcamod.available = fake.run, lambda: True
        ok, said = hr.enact("rename", "oldname", to="newname", why="the name it should have had")
        check("label: the rename went through", ok and "newname" in paths.list_agents(), said)
        check("label: Orca's worktree is now named for the agent, and the message says so",
              fake.names[str(wt)] == "newname" and "Orca now labels" in said, said + str(fake.names))
        sets = [c for c in fake.calls if c[1] == "set"]
        check("label: exactly one set, on that path, with the new name and nothing else",
              len(sets) == 1 and sets[0] == ["worktree", "set", "--worktree", f"path:{wt}",
                                            "--display-name", "newname", "--json"], str(sets))
        check("label: the checkout was not moved and the recorded worktree is the same",
              wt.is_dir() and db.get_agent_state(con, "newname")["worktree"] == f"repo-id::{wt}")

        # `hr relabel` is the same call for a rename already applied.
        fake.names[str(wt)] = "stale"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = climod.main(["hr", "relabel", "newname"])
        check("label: hr relabel names it again, for a rename that was applied before",
              code == 0 and fake.names[str(wt)] == "newname" and "Orca now labels" in out.getvalue(),
              out.getvalue() + err.getvalue())

        # Orca says ok and does not change the name: not reported as done.
        fake.sets, fake.names[str(wt)] = False, "stale"
        done, said = hr.label(con, "newname")
        check("label: an ok that changed nothing is not a label",
              not done and "would not set the label" in said
              and f"--display-name newname" in said, said)

        # Orca is not there: the rename stands, and the command to run is given.
        orcamod.available = lambda: False
        done, said = hr.label(con, "newname")
        check("label: with no Orca on PATH it says what to run instead",
              not done and "not on PATH" in said and f"path:{wt}" in said, said)
        orcamod.available = lambda: True

        # Nothing recorded, or a folder that is gone: nothing to label, no call made.
        n = len(fake.calls)
        agents.register("bare")
        db.set_terminal(con, "marina", "term_y", f"repo-id::{home / 'gone'}")
        check("label: an agent with no worktree, or a folder that is gone, is left alone",
              hr.label(con, "bare") == (True, "") and hr.label(con, "marina") == (True, "")
              and len(fake.calls) == n, str(fake.calls[n:]))
        check("label: nothing but show and set ever reached Orca",
              all(c[0] == "worktree" and c[1] in ("show", "set") for c in fake.calls))
    finally:
        orcamod._run, orcamod.available = real_run, real_avail
        con.close()


def test_a_renamed_agents_worktree_follows_it_with_its_work_and_memory(home: Path) -> None:
    """harbor #753: a rename leaves Orca's label right (`hr.label`, already
    covered by test_a_rename_names_the_agents_orca_worktree) but the folder,
    and so Claude's per-project memory, on the old name. `ocp hr rehome` is
    the other half - actually moving it. Driven on a throwaway git repo with
    an uncommitted file, two accounts' memory and a transcript."""
    _daemon_home(home, 20)
    accounts.register("beta")
    repo = home / "repo"
    repo.mkdir()
    def git(*a, cwd=repo):
        return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)
    git("init", "-q", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    old_wt = home / "wts" / "marina"
    git("worktree", "add", "-q", "-b", "work", str(old_wt))
    (old_wt / "Strings.swift").write_text("uncommitted\n")
    old_key = accounts.memory_project_dir("alpha", str(old_wt))
    new_wt = home / "wts" / "harbor.marina"
    new_key = accounts.memory_project_dir("alpha", str(new_wt))
    for acct in ("alpha", "beta"):
        proj = vendors.config_dir(acct) / "projects" / old_key
        (proj / "memory").mkdir(parents=True)
        (proj / "memory" / "note.md").write_text(f"remember {acct}\n")
        (proj / "sess-1.jsonl").write_text("{}\n")
    agents.register("harbor.marina")
    con = db.connect()
    real_live = obsmod.live_sessions
    try:
        # As `ocp launch` records it: an Orca selector, not a bare path.
        db.set_terminal(con, "harbor.marina", "term_m", f"repo-id::{old_wt}")
        obsmod.live_sessions = lambda: {"harbor.marina": 123}
        ok, said = rehome.move(con, "harbor.marina", "marina")
        check("rehome: refused while the session is live",
              not ok and "session running" in said and old_wt.is_dir(), said)
        obsmod.live_sessions = lambda: None
        ok, said = rehome.move(con, "harbor.marina", "marina")
        check("rehome: refused when liveness cannot be read", not ok and old_wt.is_dir(), said)
        obsmod.live_sessions = lambda: {}
        ok, said = rehome.move(con, "harbor.marina", "someone-else")
        check("rehome: refused when the folder does not carry the old name",
              not ok and old_wt.is_dir(), said)
        new_wt.mkdir()
        ok, said = rehome.move(con, "harbor.marina", "marina")
        check("rehome: refused when the destination exists", not ok and old_wt.is_dir(), said)
        new_wt.rmdir()

        # A destination in the way on one account: refused before anything moves.
        blocker = vendors.config_dir("beta") / "projects" / new_key
        blocker.mkdir(parents=True)
        ok, said = rehome.move(con, "harbor.marina", "marina")
        check("rehome: a memory dir already at the destination refuses, nothing moved",
              not ok and old_wt.is_dir() and (old_wt / "Strings.swift").exists()
              and not new_wt.exists()
              and all((vendors.config_dir(a) / "projects" / old_key).is_dir()
                      for a in ("alpha", "beta")), said)
        blocker.rmdir()
        # And a rename that fails on the SECOND account after the first went
        # through: the first is put back, and so is the worktree.
        real_rename = Path.rename
        def flaky(self, target):
            if "beta" in str(self) and self.name == old_key:
                raise OSError("simulated")
            return real_rename(self, target)
        Path.rename = flaky
        try:
            ok, said = rehome.move(con, "harbor.marina", "marina")
        finally:
            Path.rename = real_rename
        check("rehome: a failure on a later account undoes the earlier ones and the worktree",
              not ok and old_wt.is_dir() and (old_wt / "Strings.swift").exists()
              and not new_wt.exists()
              and all((vendors.config_dir(a) / "projects" / old_key).is_dir()
                      and not (vendors.config_dir(a) / "projects" / new_key).exists()
                      for a in ("alpha", "beta")), said)
        # Orca as measured: the old entry has a comment and a parent; after the
        # move it knows the new path only as an automatic entry.
        calls: list[list[str]] = []
        real_avail, real_run, real_sleep = orcamod.available, orcamod._run, time.sleep
        moved_yet = {"v": False}
        def fake_run(args, timeout=180):
            calls.append(args)
            if args[:2] == ["worktree", "show"]:
                if args[3] == f"path:{old_wt}":
                    w = {"id": f"repo-id::{old_wt}", "comment": "onboarding",
                         "parentWorktreeId": "repo-id::/p",
                         "workspaceStatus": "in-progress",
                         "childWorktreeIds": ["repo-id::/kid"]}
                elif moved_yet["v"]:
                    w = {"comment": "", "displayNameMode": "automatic"}
                else:
                    w = None
                return (w is not None), json.dumps({"ok": w is not None,
                                                    "result": {"worktree": w or {}}})
            if args[:2] == ["worktree", "list"]:
                return True, json.dumps({"ok": True, "result": {"worktrees": []}})
            if args[:2] == ["terminal", "list"]:
                return True, json.dumps({"ok": True, "result": {"terminals": tabs["v"]}})
            return True, json.dumps({"ok": True})
        tabs = {"v": []}
        orcamod.available = lambda: True
        orcamod._run = fake_run
        time.sleep = lambda s_: moved_yet.update(v=True)
        try:
            tabs["v"] = [{"worktreePath": str(old_wt), "handle": "term_1"}]
            ok, said = rehome.move(con, "harbor.marina", "marina")
            check("rehome: refused while a terminal is open in it",
                  not ok and "terminal" in said and old_wt.is_dir(), said)
            tabs["v"] = []
            ok, said = rehome.move(con, "harbor.marina", "marina")
        finally:
            orcamod.available, orcamod._run, time.sleep = real_avail, real_run, real_sleep
        sets = [c for c in calls if c[:2] == ["worktree", "set"]]
        check("rehome: Orca's new entry is named for the agent, fixed, with the old "
              "entry's comment, parent and status carried",
              len(sets) == 2 and sets[0][3] == f"path:{new_wt}"
              and sets[0][sets[0].index("--display-name") + 1] == "harbor.marina"
              and sets[0][sets[0].index("--comment") + 1] == "onboarding"
              and sets[0][sets[0].index("--parent-worktree") + 1] == "id:repo-id::/p"
              and sets[0][sets[0].index("--workspace-status") + 1] == "in-progress",
              str(calls))
        kid = [c for c in sets if c[3] == "id:repo-id::/kid"]
        check("rehome: a child of the moved worktree is pointed at the new path",
              len(kid) == 1 and kid[0][kid[0].index("--parent-worktree") + 1] == f"path:{new_wt}",
              str(calls))
        check("rehome: the move waited for Orca to see the new path before setting it",
              moved_yet["v"] and "not set" not in said, said)
        check("rehome: moves once closed", ok and new_wt.is_dir() and not old_wt.exists(), said)
        check("rehome: the uncommitted file survived the move",
              (new_wt / "Strings.swift").read_text() == "uncommitted\n")
        listed = git("worktree", "list", "--porcelain").stdout
        check("rehome: git's own records follow (worktree list, branch, status)",
              str(new_wt) in listed and str(old_wt) not in listed
              and "?? Strings.swift" in git("status", "--short", cwd=new_wt).stdout
              and git("branch", "--show-current", cwd=new_wt).stdout.strip() == "work")
        for acct in ("alpha", "beta"):
            root = vendors.config_dir(acct) / "projects"
            check(f"rehome: {acct}'s memory and transcript are under the new key",
                  (root / new_key / "memory" / "note.md").read_text() == f"remember {acct}\n"
                  and (root / new_key / "sess-1.jsonl").is_file() and not (root / old_key).exists())
        row = db.get_agent_state(con, "harbor.marina")
        check("rehome: the recorded worktree keeps its selector form and names the new folder",
              row["worktree"] == f"repo-id::{new_wt}" and row["terminal"] is None,
              str(dict(row)))
        ok, said = rehome.move(con, "harbor.marina", "marina")
        check("rehome: run again it is a no-op that says so", not ok and "already" in said, said)

        # A blocked read of Orca is a refusal to move, not a licence to.
        for reply, want in ((lambda a, timeout=180: (False, "timeout"), True),
                            (lambda a, timeout=180: (False, json.dumps(
                                {"ok": False, "error": {"code": "selector_not_found"}})), False)):
            orcamod.available, orcamod._run = (lambda: True), reply
            try:
                blocked = rehome._orca_blockers(new_wt)
            finally:
                orcamod.available, orcamod._run = real_avail, real_run
            check(f"rehome: Orca {'unreadable' if want else 'not knowing the path'} "
                  f"{'refuses' if want else 'does not block'}", bool(blocked) == want, blocked)
        # A terminal list that fails after a good show is no licence to move.
        def show_only(args, timeout=180):
            if args[:2] == ["worktree", "show"]:
                return True, json.dumps({"ok": True, "result": {"worktree": {"id": "i"}}})
            return False, "timeout"
        orcamod.available, orcamod._run = (lambda: True), show_only
        try:
            blocked = rehome._orca_blockers(new_wt)
        finally:
            orcamod.available, orcamod._run = real_avail, real_run
        check("rehome: a failed terminal list refuses the move", "unknown" in blocked, blocked)
        # The move happened but Orca never shows the new path: the children's
        # commands are printed, not dropped.
        old_entry = {"id": "i", "childWorktreeIds": ["repo-id::/kid"]}
        orcamod.available = lambda: True
        orcamod._run = lambda a, timeout=180: (False, json.dumps(
            {"ok": False, "error": {"code": "selector_not_found"}}))
        time.sleep = lambda s_: None
        try:
            said = rehome._relabel(old_entry, Path("/new/place"), "harbor.marina")
        finally:
            orcamod.available, orcamod._run, time.sleep = real_avail, real_run, real_sleep
        check("rehome: when Orca never sees the new path, each child's re-parent command is printed",
              "orca worktree set --worktree id:repo-id::/kid --parent-worktree path:/new/place"
              in said and "--display-name harbor.marina" in said, said)
        # Orca seeing it but refusing a set: its reason is kept.
        def refuses(args, timeout=180):
            if args[:2] == ["worktree", "show"]:
                return True, json.dumps({"ok": True, "result": {"worktree": {"id": "n"}}})
            return False, json.dumps({"ok": False, "error": {"code": "busy_now"}})
        orcamod.available, orcamod._run = (lambda: True), refuses
        try:
            said = rehome._relabel(old_entry, Path("/new/place"), "harbor.marina")
        finally:
            orcamod.available, orcamod._run = real_avail, real_run
        check("rehome: a refused label and a refused re-parent both say why",
              said.count("busy_now") == 2 and "would not set the label" in said
              and "would not re-parent repo-id::/kid" in said, said)
    finally:
        obsmod.live_sessions = real_live
        con.close()


def test_hr_rehome_cli_moves_the_folder(home: Path) -> None:
    """The `ocp hr rehome` leaf itself, not just rehome.move()."""
    _daemon_home(home, 20)
    repo = home / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                    "-q", "--allow-empty", "-m", "x"], cwd=repo, check=True)
    old_wt = home / "wts" / "old"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "w", str(old_wt)],
                   cwd=repo, check=True)
    new_wt = home / "wts" / "newname"
    agents.register("newname")
    con = db.connect()
    try:
        db.set_terminal(con, "newname", "term_x", str(old_wt))
    finally:
        con.close()
    real_live = obsmod.live_sessions
    obsmod.live_sessions = lambda: {}
    try:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = climod.main(["hr", "rehome", "newname", "--from", "old"])
        check("hr rehome: the CLI leaf moves the folder and reports it",
              code == 0 and new_wt.is_dir() and not old_wt.exists()
              and "worktree moved" in out.getvalue(), out.getvalue() + err.getvalue())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = climod.main(["hr", "rehome", "newname", "--from", "old"])
        check("hr rehome: run again on an already-moved agent refuses, exit 1",
              code == 1 and "already" in out.getvalue(), out.getvalue())
    finally:
        obsmod.live_sessions = real_live


def test_agent_yaml_is_written_by_dump_so_what_was_typed_reads_back(home: Path) -> None:
    """`ocp hr add --model 4.5` wrote `model: 4.5`, which reads back as a float,
    and `--model 2026-09-20` wrote a file that did not load. The file is built as
    a map and written by dump, which quotes what a reader would take for
    something else, so each value reads back as the text that was typed."""
    restore_world()
    use_home(home)
    agents.register("plain")
    agents.register("full", model="opus", effort="high", weight="heavy",
                    repo="/Users/ron/some repo")
    check("agent.yaml: the ordinary file is byte for byte what it was",
          agents.agent_file("plain").read_text()
          == f"name: plain\nmodel: {agents.DEFAULT_MODEL}\n"
             f"effort: {agents.DEFAULT_EFFORT}\nweight: light\nenabled: true\n"
          and agents.agent_file("full").read_text()
          == "name: full\nmodel: opus\neffort: high\nweight: heavy\n"
             "enabled: true\nrepo: /Users/ron/some repo\n")
    typed = [("model", "2026-09-20"), ("model", "no"), ("model", "4.5"), ("weight", "on"),
             ("repo", "1_000")]
    for i, (field, value) in enumerate(typed):
        agents.register(f"agent{i}", **{field: value})
        cfg = agents.read(f"agent{i}")
        check(f"agent.yaml: --{field} {value} reads back as that text",
              cfg[field] == value and cfg["enabled"] is True and cfg["name"] == f"agent{i}",
              repr(cfg))
        if yamlcompat.HAVE_PYYAML:
            import yaml as pyyaml
            check(f"agent.yaml: and PyYAML reads --{field} {value} the same",
                  pyyaml.safe_load(agents.agent_file(f"agent{i}").read_text()) == cfg)
    # What the replay does: apply builds the agent from the saved proposal's fields.
    hand_edit_team(team.STARTER)
    ok, said = hr._add({"agent": "replayed", "model": "4.5",
                        "effort": "high", "repo": "1_000", "why": "w",
                        "requested_by": "human", "created": int(time.time())}, None)
    cfg = agents.read("replayed")
    check("agent.yaml: an applied proposal carrying such values leaves a file that loads",
          ok and cfg["model"] == "4.5" and cfg["repo"] == "1_000" and cfg["effort"] == "high",
          f"{said} {cfg}")
    try:
        agents.register("ctrl", model="a\nb")
        check("agent.yaml: a control character is refused", False)
    except yamlcompat.ConfigError:
        check("agent.yaml: a control character is refused, and leaves no folder behind",
              not paths.agent_dir("ctrl").exists())
    # A refusal leaves nothing behind: the effort is checked before the folder is made.
    try:
        agents.register("badeffort", effort="bogus")
        check("agent.yaml: an effort that is not one of ours is refused", False)
    except ValueError:
        check("agent.yaml: an effort that is not one of ours is refused, and leaves no folder",
              not paths.agent_dir("badeffort").exists())
    agents.register("plain", model="4.5")
    check("agent.yaml: registering again updates through the same writer",
          agents.read("plain")["model"] == "4.5")


def test_yaml_booleans() -> None:
    got = yamlcompat.parse("a: yes\nb: no\nc: on\nd: off\ne: true\nf: false\n")
    check("yaml: YAML 1.1 booleans are booleans, not strings",
          got == {"a": True, "b": False, "c": True, "d": False, "e": True, "f": False},
          str(got))
    check("yaml: a quoted 'no' stays a string",
          yamlcompat.parse('a: "no"\n')["a"] == "no")


def test_team_validation() -> None:
    old = team.Team(yamlcompat.parse("agents:\n  a: { intake: nonsense, model: mid }\n"))
    check("team validation: an old team.yaml's intake key is ignored, not an error",
          "a" in old.agents)
    for bad, label in [
        ('edges:\n  - { from: "a", to: "b", response: blocking }\n',
         "blocking response rejected"),
        ('edges:\n  - { to: "b" }\n', "edge without 'from' rejected"),
    ]:
        try:
            team.Team(yamlcompat.parse(bad))
            check(f"team validation: {label}", False, "no error raised")
        except yamlcompat.ConfigError:
            check(f"team validation: {label}", True)


def test_team_deny_validation() -> None:
    """A malformed deny silently *allows*, so it must fail to load."""
    base = ("teams:\n  eng: [backend]\n  growth: [marketing]\n"
            "agents:\n  backend: {}\n  marketing: {}\n"
            'edges:\n  - { from: "*", to: "*", relation: peer }\n')
    for deny, label in [
        ('deny:\n  - { form: "backend", to: "marketing", reason: "typo key" }\n',
         "a typo'd key"),
        ('deny:\n  - { from: "backend" }\n', "a missing 'to'"),
        ('deny:\n  - { from: "engg.*", to: "growth.*" }\n', "an unknown team"),
    ]:
        try:
            team.Team(yamlcompat.parse(base + deny))
            check(f"team: rejects {label} in deny", False, "loaded instead of raising")
        except yamlcompat.ConfigError:
            check(f"team: rejects {label} in deny", True)

    # An unknown team in an edge is just as silent.
    try:
        team.Team(yamlcompat.parse(
            "teams:\n  eng: [backend]\n"
            "agents:\n  backend: {}\n"
            'edges:\n  - { from: "nosuch.*", to: "backend" }\n'))
        check("team: rejects an unknown team in an edge", False, "loaded")
    except yamlcompat.ConfigError:
        check("team: rejects an unknown team in an edge", True)

    # And a well-formed deny must still win.
    t = team.Team(yamlcompat.parse(
        base + 'deny:\n  - { from: "eng.*", to: "growth.*", reason: "via product" }\n'))
    check("team: a well-formed deny still wins over a wildcard edge",
          not t.route("backend", "marketing").allowed)


def test_keychain_service_is_keyed_by_the_user_home(home: Path) -> None:
    import hashlib
    from ocp.vendors.claude import keychain_service
    check("keychain: the default config dir under the user home is the bare service",
          keychain_service(paths.user_home() / ".claude") == "Claude Code-credentials")
    other = paths.user_home() / ".orcapipe" / "accounts" / "alpha" / ".claude"
    digest = hashlib.sha256(str(other).encode()).hexdigest()[:8]
    check("keychain: any other dir is suffixed with its path's sha256",
          keychain_service(other) == f"Claude Code-credentials-{digest}", keychain_service(other))


def test_a_claude_agent_is_never_placed_on_another_vendors_account(home: Path) -> None:
    """#1776: a second vendor's account that reads as logged in and has room
    is still never a place for a claude agent - it would run claude against
    that vendor's credentials. Until accounts carried a vendor the only thing
    keeping one out was `logged_in` asking claude's Keychain and getting a
    no, an accident. Here the account is logged in by its own driver and
    reads better than anything else, so each place that picks an account is
    asked, and only the vendor keeps it out."""
    _daemon_home(home, 99)
    now = int(time.time())
    # Far enough from its reset that a move beats waiting for it.
    write_status(home, "alpha", pct_5h=99, pct_7d=10, ctx_pct=5, reset_5h=now + 4 * 3600)
    real = (accounts.logged_in, orcamod.available)
    with second_vendor(metered=True):
        try:
            accounts.register("alpha")
            (paths.account_dir("alpha") / ".claude" / ".credentials.json").write_text("{}")
            accounts.register("zeta", vendor="zai")
            write_status(home, "zeta", pct_5h=5, pct_7d=5, reset_5h=now + 1800,
                         reset_7d=now + 90000, rl_at=now)
            check("vendor pick: the premise - zeta is logged in by its own driver",
                  accounts.logged_in("zeta"))
            check("vendor pick: and has room, so nothing but its vendor can keep it out",
                  status.read("zeta").takes_work()[0], str(status.read("zeta").takes_work()))

            check("vendor pick: find_room does not send the walled claude agent there",
                  launch.find_room("backend") != "zeta", str(launch.find_room("backend")))
            picked, ranked = status.pick_account(bootstrap=True, agent="backend")
            check("vendor pick: pick_account for the agent does not take it",
                  picked != "zeta" and "a zai account, not claude" in dict(ranked)["zeta"],
                  str((picked, ranked)))
            check("vendor pick: nor pick_account for no agent in particular",
                  status.pick_account()[0] != "zeta", str(status.pick_account()))
            con = db.connect()
            try:
                db.add_request(con, "orcapipe", "backend", "work", None, "supervisor",
                               "async", None, 0, None)
                plan = dmn_handoff._move_plan(con, "backend", "usage_5h")
                check("vendor pick: the daemon's move off the wall does not go there",
                      plan[0] is None and "zeta: a zai account, not claude" in plan[1], str(plan))
                # The page and the panel carry no agent, so their verdict is
                # the account's own: judged for the vendor whose sessions are
                # the only ones that can ever run here (#1815). Drawn as a
                # refusal it said "assign no - a zai account, not claude" of an
                # account that is a perfectly good place for zai agents.
                facts = actions.account_facts(con, "zeta")
                allowed, why = facts["assign"]
                check("vendor pick: its page does not refuse it outright - the "
                      "account is judged for its own vendor",
                      allowed and "not claude" not in why, why)
                check("vendor pick: and the page still names the vendor",
                      facts["vendor"] == "zai" and facts["config"].endswith("(zai)"), str(facts))
            finally:
                con.close()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                clihealth.cmd_status(argparse.Namespace(json=True))
            check("vendor pick: ocp status does not name it next",
                  json.loads(out.getvalue())["picked"] != "zeta", out.getvalue()[:200])
            accounts.logged_in = lambda a: True
            rows = [tui._ANSI.sub("", ln) for ln in _snap(120, 40).split(chr(10))]
            zrow = [ln for ln in rows if ln.lstrip("│ ").startswith("zeta ")]
            check("vendor pick: the accounts panel does not mark it next",
                  zrow and "next" not in zrow[0], str(zrow))
            check("vendor pick: its tick is its own vendor's answer, not a ✘ "
                  "that reads as a broken account",
                  zrow and "✔" in zrow[0], str(zrow))
            check("vendor pick: a fold is not sent there",
                  foldmod._cheapest_account() != "zeta", str(foldmod._cheapest_account()))

            # The control: a zai agent is placed there, so the filter is on
            # the vendor, not on the account.
            agents.register("glm")
            _with_vendor("glm", "zai")
            check("vendor pick: a zai agent is placed on the zai account",
                  launch.find_room("glm") == "zeta"
                  and status.pick_account(agent="glm")[0] == "zeta",
                  str((launch.find_room("glm"), status.pick_account(agent="glm"))))
        finally:
            accounts.logged_in, orcamod.available = real


def test_another_vendors_account_never_spends_a_live_refresh(home: Path) -> None:
    """auditor #1863: the vendor refusal runs before live_refresh. A stale zai
    account listed ahead of a stale claude one must not be asked about, or it
    spends the call's only refresh on an account the agent can never use and
    the claude one is refused on its stale reading."""
    _daemon_home(home, None)
    now = int(time.time())
    real_read, real_login = usage_api.read, accounts.logged_in
    asked: list[str] = []
    with second_vendor(metered=True):
        try:
            accounts.register("zeta", vendor="zai")
            for n in ("zeta", "alpha"):
                write_status(home, n, pct_5h=90, reset_5h=now - 60, rl_carried=False,
                             rl_at=now - status.CARRY_MAX_AGE_S - 300, collected_at=now)
                check(f"vendor refresh: the premise - {n} is carried_old, so a refresh "
                      "would ask about it", status.read(n).short_source == "carried_old",
                      status.read(n).short_source)

            def fake_read(acct, refresh=True):
                asked.append(acct)
                return reading_of(acct, 10, now + 9000, 20, now + 90000, now)
            usage_api.read = fake_read
            accounts.logged_in = lambda acct: True

            picked, ranked = status.pick_account(["zeta", "alpha"], agent="backend",
                                                 live_refresh=True)
            check("vendor refresh: the zai account is never asked about",
                  "zeta" not in asked, asked)
            check("vendor refresh: it is refused as another vendor's",
                  dict(ranked).get("zeta") == "a zai account, not claude", ranked)
            check("vendor refresh: the claude account gets the refresh and the agent",
                  asked == ["alpha"] and picked == "alpha", (asked, picked, ranked))
        finally:
            usage_api.read, accounts.logged_in = real_read, real_login


def test_a_second_vendors_live_session_is_seen_by_the_sweep(home: Path) -> None:
    """#1815: `is_session` decided from claude's own literals, so a session of
    any other vendor matched nothing at all. `live_sessions` then left that
    agent's pid None, and `reconcile` reads "terminal alive, no process" as the
    session having died - so the daemon recorded a live, correctly placed agent
    closed, on every tick. Which binary a session runs, and how the vendor
    spells a headless run, are both the driver's answers now.

    Driven through real `ps` command lines rather than by patching
    `live_sessions`: every other test in this file replaces that function
    wholesale, so none of them reaches `is_session` at all."""
    _daemon_home(home, 20)
    with second_vendor(metered=True):
        check("is_session: a second vendor's own binary is a session",
              obsmod.is_session("/opt/zai/bin/glmcode --model glm-4.6"))
        check("is_session: claude's still is",
              obsmod.is_session("/usr/local/bin/claude --resume abc123"))
        check("is_session: the second vendor's own spelling of headless is not",
              not obsmod.is_session("/opt/zai/bin/glmcode run 'fold it'"))
        check("is_session: nor is claude's",
              not obsmod.is_session("/usr/local/bin/claude -p 'fold it'"))
        check("is_session: and nothing of ours is not a session either",
              not obsmod.is_session("/usr/bin/ssh somewhere"))
    check("is_session: with that driver gone its command line is not a session",
          not obsmod.is_session("/opt/zai/bin/glmcode --model glm-4.6"))

    # And the invariant the miss actually broke: the daemon's own record of a
    # running agent. Real `live_sessions`, over a process table we supply.
    real = (obsmod.live_sessions, obsmod.process_table, obsmod.process_env,
            orcamod.available, orcamod.live_terminals)
    obsmod.live_sessions = REAL_OBSERVE["live_sessions"]
    orcamod.available = lambda: True
    orcamod.live_terminals = lambda: {"term_x", "term_z"}
    with second_vendor(metered=True):
        try:
            accounts.register("zeta", vendor="zai")
            agents.register("glm")
            _with_vendor("glm", "zai")
            con = db.connect()
            try:
                db.set_terminal(con, "glm", "term_z", "wt")
                db.set_agent_state(con, "glm", dmn_states.RUNNING, detail="working")
                table = [(4301, "/opt/zai/bin/glmcode --model glm-4.6")]
                obsmod.process_table = lambda: list(table)
                obsmod.process_env = lambda pid: f"OCP_AGENT={'glm' if pid == 4301 else 'x'}"
                check("vendor sweep: the premise - its pid is found",
                      obsmod.live_sessions() == {"glm": 4301},
                      str(obsmod.live_sessions()))
                said: list[str] = []
                dmn_reconciler.sweep(con, lambda _m: None, said)
                row = db.get_agent_state(con, "glm")
                check("vendor sweep: a live second-vendor session is not recorded closed",
                      not row["closed_at"]
                      and not any("glm: its session is gone" in a for a in said),
                      str((dict(row), said)))

                # The control, on the same fixture: when its process really has
                # gone, the same sweep does record it closed - so the check
                # above is about the vendor and not about a sweep that never
                # reached this agent.
                table.clear()
                said = []
                dmn_reconciler.sweep(con, lambda _m: None, said)
                row = db.get_agent_state(con, "glm")
                check("vendor sweep: and when it really is gone, it is recorded closed",
                      row["closed_at"]
                      and any("glm: its session is gone" in a for a in said),
                      str((dict(row), said)))
            finally:
                con.close()
        finally:
            (obsmod.live_sessions, obsmod.process_table, obsmod.process_env,
             orcamod.available, orcamod.live_terminals) = real


def test_an_accounts_verdict_is_asked_for_its_own_vendor(home: Path) -> None:
    """#1815: the panel's tick and the page's sentence arrive with no agent in
    hand, so a claude-only verdict said "no - a zai account, not claude" about
    an account that is a perfectly good place for the agents it is for, and a
    human reads that as an account gone wrong. It is judged for its own vendor
    now. What is genuinely absolute - unmetered, no driver, not logged in -
    still refuses, and no placement path is touched: those all hold an agent,
    and the test above is what pins them."""
    _daemon_home(home, 20)
    now = int(time.time())
    with second_vendor(metered=True):
        accounts.register("zeta", vendor="zai")
        write_status(home, "zeta", pct_5h=5, pct_7d=5, reset_5h=now + 1800,
                     reset_7d=now + 90000, rl_at=now)
        ok, why = status.account_verdict("zeta", status.read("zeta"), None, [], True)
        check("verdict: a healthy second-vendor account is not refused outright",
              ok and "not claude" not in why, str((ok, why)))

        _, ranked = status.pick_account(["alpha", "zeta"], vendor="zai")
        check("verdict: pick_account ranks for the vendor it is given",
              "a claude account, not zai" in dict(ranked)["alpha"]
              and "not zai" not in dict(ranked)["zeta"], str(ranked))
        ok, why = status.account_verdict("zeta", status.read("zeta"), None, ranked, True)
        check("verdict: and that ranking's words are the ones drawn",
              ok and why == dict(ranked)["zeta"], str((ok, why)))

        agents.register("glm")
        _with_vendor("glm", "zai")
        _, ranked2 = status.pick_account(["alpha", "zeta"], agent="glm", vendor="claude")
        check("verdict: an agent's own vendor wins over a vendor named beside it",
              "a claude account, not zai" in dict(ranked2)["alpha"], str(ranked2))

        check("verdict: a claude account's own verdict is unchanged",
              status.account_verdict("alpha", status.read("alpha"), None, [], True)[0])
        check("verdict: and not logged in is still a no, vendor or no vendor",
              status.account_verdict("zeta", status.read("zeta"), None, [], False)
              == (False, "not logged in"))

    # The refusals that are true of every agent there is, its own included.
    with second_vendor(metered=False):
        ok, why = status.account_verdict("zeta", status.read("zeta"), None, [], True)
        check("verdict: an unmetered account is still refused, in its own words",
              not ok and why == status.UNMETERED_WHY, str((ok, why)))
    # No driver at all: `not_for` caught this only because the vendor asked
    # was always claude, so it needs its own refusal now.
    (paths.account_dir("zeta") / "vendor").write_text("nosuch\n")
    ok, why = status.account_verdict("zeta", status.read("zeta"), None, [], True)
    check("verdict: an account whose vendor has no driver is still refused",
          not ok and "no driver for vendor 'nosuch'" in why, str((ok, why)))


def test_an_unmetered_account_is_pinned_never_picked_or_polled(home: Path) -> None:
    """#1776: an account whose vendor reports no usage is its own state, not
    "no reading yet" - that one is a bootstrap candidate, and nothing will
    ever give this one a reading. Its own agents start there; nothing is
    moved onto it, it is never asked for usage, and it is drawn unmetered."""
    _daemon_home(home, 20)
    real = (usage_api.read, claudemeter.refresh_credential, accounts.logged_in)
    with second_vendor(metered=False):
        try:
            accounts.register("alpha")
            (paths.account_dir("alpha") / ".claude" / ".credentials.json").write_text("{}")
            accounts.register("zeta", vendor="zai")
            accounts.register("zeta2", vendor="zai")
            agents.register("glm")
            _with_vendor("glm", "zai")
            st = status.read("zeta")
            check("unmetered: read as its own state, not as no reading yet",
                  st.unmetered and st.assignable() == (False, status.UNMETERED_WHY),
                  str((st.unmetered, st.assignable())))
            picked, ranked = status.pick_account(["zeta2"], bootstrap=True, agent="glm")
            check("unmetered: never picked, not even as a bootstrap last resort",
                  picked is None and dict(ranked)["zeta2"] == status.UNMETERED_WHY,
                  str((picked, ranked)))
            check("unmetered: nor is it a place a new session is put",
                  launch.find_room("glm") is None, str(launch.find_room("glm")))
            con = db.connect()
            try:
                db.set_account(con, "glm", "zeta")
                con.commit()
            finally:
                con.close()
            argv, env = launch.build("glm", ["--resume", "x"])
            check("unmetered: and launches as its own vendor, into its own config dir",
                  argv[0] == "/opt/zai/bin/glmcode"
                  and env.get("ZAI_HOME") == str(vendors.config_dir("zeta")),
                  str((argv[:1], env.get("ZAI_HOME"))))

            asked: list[str] = []
            usage_api.read = lambda acct, refresh=True: asked.append(acct)
            real_recover = recovermod.recover
            recovermod.recover = lambda acct: (False, "stub: not under test here")
            con = db.connect()
            try:
                # One account per tick, each put on its cooldown once asked,
                # so four ticks walk past every account there is.
                for _ in range(4):
                    dmn_usage_poll.poll_usage(con, lambda _m: None)
            finally:
                con.close()
                recovermod.recover = real_recover
            check("unmetered: the usage poll never asks about it",
                  "alpha" in asked and not {"zeta", "zeta2"} & set(asked), str(asked))
            usage_api.read = real[0]
            renewed: list[str] = []
            claudemeter.refresh_credential = lambda acct, **k: renewed.append(acct) or True
            check("unmetered: nor does the endpoint reader, and it renews nothing",
                  usage_api.read("zeta") is None and not renewed, str(renewed))

            accounts.logged_in = lambda a: True
            rows = [tui._ANSI.sub("", ln) for ln in _snap(120, 40).split(chr(10))]
            zrow = [ln for ln in rows if ln.lstrip("│ ").startswith("zeta ")]
            check("unmetered: the accounts panel says so",
                  zrow and "unmetered" in zrow[0], str(zrow))
            accounts.logged_in = real[2]
            r = doctor.Report()
            doctor._check_accounts(r)
            check("unmetered: doctor says so rather than warning of no status.json",
                  any(n == "account zeta: unmetered (zai)" for _lv, n, _d in r.rows)
                  and not any(n.startswith("account zeta: no status") for _lv, n, _d in r.rows),
                  str([n for _lv, n, _d in r.rows if "zeta" in n]))
        finally:
            usage_api.read, claudemeter.refresh_credential, accounts.logged_in = real


def test_an_account_records_its_vendor(home: Path) -> None:
    """The vendor is written where the account is, once, and every account
    answer comes from it; one that predates vendors is claude's."""
    os.environ["OCP_HOME"] = str(home)
    accounts.register("alpha")
    check("vendor record: an account that records none is the default's",
          vendors.account_vendor("alpha") == "claude"
          and not (paths.account_dir("alpha") / "vendor").exists())
    try:
        accounts.register("nope", vendor="nosuch")
        check("vendor record: a vendor with no driver is refused", False, "registered")
    except ValueError as e:
        check("vendor record: a vendor with no driver is refused, making nothing",
              "no driver for vendor 'nosuch'" in str(e) and "nope" not in paths.list_accounts(),
              str(e))
    agents.register("broken")
    agents.agent_file("broken").write_text("- not a map\n")
    picked, ranked = status.pick_account(["alpha"], agent="broken")
    check("vendor record: an agent whose vendor cannot be read is placed nowhere",
          picked is None and dict(ranked)["alpha"].startswith("agent.yaml unreadable"),
          str((picked, ranked)))
    agents.agent_file("broken").write_bytes(b"role: \xff\xfe\n")
    try:
        agents.read("broken")
        check("agents.read: a non-UTF-8 agent.yaml is a ConfigError", False, "read")
    except yamlcompat.ConfigError as e:
        check("agents.read: a non-UTF-8 agent.yaml is a ConfigError", "UTF-8" in str(e), str(e))
    with second_vendor():
        accounts.register("zeta", vendor="zai")
        check("vendor record: a second vendor is written down",
              vendors.account_vendor("zeta") == "zai")
        accounts.register("zeta")
        check("vendor record: re-registering without one keeps it",
              vendors.account_vendor("zeta") == "zai")
        try:
            accounts.register("zeta", vendor="claude")
            check("vendor record: an account's vendor is not changed under it", False, "changed")
        except ValueError as e:
            check("vendor record: an account's vendor is not changed under it",
                  "already a zai account" in str(e) and vendors.account_vendor("zeta") == "zai",
                  str(e))
        (paths.account_dir("zeta") / "vendor").write_text("")
        check("vendor record: an empty record names no vendor, so the account is not usable",
              not accounts.logged_in("zeta") and status.pick_account(["zeta"], bootstrap=True)[0] is None)


def test_shared_dir_is_named_by_the_vendors_own_config_dir_name(home: Path) -> None:
    """shared/<config_dir_name> is where each vendor's shared items live;
    claude's keeps its historical name (design/04 §3: nothing on disk is
    renamed for the vendor already in use)."""
    os.environ["OCP_HOME"] = str(home)
    with second_vendor():
        check("vendors.shared_dir: claude's is unchanged",
              vendors.shared_dir("claude") == paths.home() / "shared" / ".claude")
        check("vendors.shared_dir: another vendor gets its own",
              vendors.shared_dir("zai") == paths.home() / "shared" / ".zai")


def test_an_account_links_from_its_own_vendors_shared_dir(home: Path) -> None:
    """register(), relink_all() and unlinked() read `drv.shared_items()` from
    shared/<that account's own vendor>, not from claude's - a "skills" item
    dropped into a second vendor's shared dir reaches that vendor's accounts,
    never claude's shared/.claude (design/04 §4.1)."""
    os.environ["OCP_HOME"] = str(home)
    with second_vendor() as drv:
        drv.never_touch = lambda: set()  # SecondVendor's own default excludes "skills"
        accounts.register("zeta", vendor="zai")
        link = vendors.config_dir("zeta") / "skills"
        check("register: no link yet - the second vendor's shared dir is empty",
              not link.exists())
        (vendors.shared_dir("zai") / "skills").mkdir(parents=True)
        (vendors.shared_dir("claude") / "skills").mkdir(parents=True, exist_ok=True)
        accounts.register("zeta", vendor="zai")
        check("register: linked from the second vendor's own shared dir",
              link.is_symlink() and os.readlink(link) == str(vendors.shared_dir("zai") / "skills"))
        link.unlink()
        done = accounts.relink_all()
        check("relink_all: catches it up from the same, vendor-owned, shared dir",
              done.get("zeta") == ["skills"]
              and os.readlink(link) == str(vendors.shared_dir("zai") / "skills"), str(done))
        link.unlink()
        link.symlink_to(home / "elsewhere")
        check("unlinked: reports it against the second vendor's own shared dir",
              ("zeta", "skills") in accounts.unlinked())


def test_register_force_keeps_what_a_real_skills_dir_held(home: Path) -> None:
    """Claude can create skills/synced before the link exists. `register
    --force` links it anyway, and the bucket moves into shared. Nothing is
    deleted that was not moved: a clash, or a move that fails, changes nothing."""
    import shutil
    os.environ["OCP_HOME"] = str(home)
    accounts.register("gamma")
    shared = vendors.shared_dir("claude") / "skills"
    (shared / "synced" / "old").mkdir(parents=True)
    (shared / "synced" / "old" / "keep").write_text("shared")
    link = vendors.config_dir("gamma") / "skills"
    link.unlink()

    def account_side(*files: str) -> None:
        shutil.rmtree(link, ignore_errors=True)
        for f in files:
            (link / f).parent.mkdir(parents=True, exist_ok=True)
            (link / f).write_text("account")

    def refused() -> str | None:
        try:
            accounts.register("gamma", force=True)
        except accounts.AdoptRefused as e:
            return str(e)
        return None

    def untouched(*files: str) -> bool:
        return (link.is_dir() and not link.is_symlink()
                and all((link / f).read_text() == "account" for f in files)
                and (shared / "synced" / "old" / "keep").read_text() == "shared")

    account_side("synced/b1/manifest.json", "synced/.bucket-b1", "synced/old/new")
    check("relink_all: a real directory is left alone", not accounts.relink_all())
    accounts.register("gamma", force=True)
    check("force: the directory is now the shared link",
          link.is_symlink() and os.readlink(link) == str(shared))
    check("force: the bucket, its marker and a file beside shared's own moved",
          (shared / "synced" / "b1" / "manifest.json").exists()
          and (shared / "synced" / ".bucket-b1").exists()
          and (shared / "synced" / "old" / "new").exists()
          and (shared / "synced" / "old" / "keep").read_text() == "shared")

    link.unlink()
    (shared / "synced" / "old" / "new").unlink()
    (shared / "dirsame").mkdir()
    account_side("dirsame", "synced/other")
    msg = refused()
    check("clash: a file where shared has a directory is refused, naming it",
          msg is not None and "dirsame" in msg, str(msg))
    check("clash: the account side is untouched", untouched("dirsame", "synced/other")
          and not (shared / "synced" / "other").exists())

    (shared / "dirsame").rmdir()
    (shared / "filesame").write_text("shared")
    account_side("filesame", "synced/other")
    check("clash: a file both have is refused too", refused() is not None)
    check("clash: and nothing moved", untouched("filesame", "synced/other")
          and (shared / "filesame").read_text() == "shared"
          and not (shared / "synced" / "other").exists())

    (shared / "filesame").unlink()
    account_side("a", "b", "synced/c")
    real_move, calls = shutil.move, []

    def flaky(a, b, *rest):
        calls.append(a)
        if len(calls) == 2:
            raise OSError("disk full")
        return real_move(a, b, *rest)
    shutil.move = flaky
    try:
        accounts.register("gamma", force=True)
        failed = False
    except OSError:
        failed = True
    finally:
        shutil.move = real_move
    check("failed move: it is raised", failed)
    check("failed move: the account is as it was and shared has none of it",
          untouched("a", "b", "synced/c")
          and not any((shared / n).exists() for n in ("a", "b")))


def test_register_force_plans_every_item_before_changing_one(home: Path) -> None:
    """A real file is replaced only when identical to shared's; otherwise the
    whole registration is refused and nothing at all is changed - not even an
    item earlier in the list that would have been fine."""
    import shutil
    os.environ["OCP_HOME"] = str(home)
    accounts.register("gamma")
    root = vendors.shared_dir("claude")
    cdir = vendors.config_dir("gamma")
    (root / "CLAUDE.md").write_text("shared rules")
    settings, claude_md, skills = cdir / "settings.json", cdir / "CLAUDE.md", cdir / "skills"
    (root / "skills").mkdir(exist_ok=True)
    settings.unlink()
    settings.write_text((root / "settings.json").read_text())
    claude_md.write_text("my own rules")
    skills.unlink()
    (skills / "synced" / "b1").mkdir(parents=True)
    (skills / "synced" / "b1" / "m.json").write_text("{}")

    def refusal() -> str | None:
        try:
            accounts.register("gamma", force=True)
        except accounts.AdoptRefused as e:
            return str(e)
        return None

    msg = refusal()
    check("file: a real file that differs is refused, naming it and saying nothing changed",
          msg is not None and "CLAUDE.md" in msg and "nothing was changed" in msg, str(msg))
    check("file: nothing changed, not even the identical settings.json or the skills dir",
          not settings.is_symlink() and not skills.is_symlink()
          and claude_md.read_text() == "my own rules"
          and (skills / "synced" / "b1" / "m.json").exists()
          and not (root / "skills" / "synced").exists())
    accounts.register("gamma", force=False)
    check("file: without --force the same files are left alone, no refusal",
          claude_md.read_text() == "my own rules" and not settings.is_symlink())

    claude_md.write_text("shared rules")
    accounts.register("gamma", force=True)
    check("file: a real file identical to shared's is replaced by the link",
          claude_md.is_symlink() and settings.is_symlink())
    check("file: and the directory's bucket moved into shared",
          (root / "skills" / "synced" / "b1" / "m.json").exists() and skills.is_symlink())

    claude_md.unlink()
    claude_md.write_text("mine")
    settings.unlink()
    settings.write_text((root / "settings.json").read_text())
    skills.unlink()
    (skills / "n1").mkdir(parents=True)
    (skills / "n1" / "f").write_text("x")
    real_move, calls = shutil.move, []

    def failing(a, b, *rest):
        raise OSError("disk full")
    shutil.move = failing
    try:
        accounts.register("gamma", force=True)
        said = None
    except (accounts.AdoptRefused, OSError) as e:
        said = str(e)
    finally:
        shutil.move = real_move
    check("file: a differing file refuses before any move is tried", said is not None
          and "CLAUDE.md" in said and "nothing was changed" in said, str(said))
    claude_md.write_text("shared rules")
    shutil.move = failing
    try:
        accounts.register("gamma", force=True)
        said = None
    except OSError as e:
        said = str(e)
    finally:
        shutil.move = real_move
    check("partial: a failure after earlier items says what was already linked",
          said is not None and "skills failed" in said and "already linked before it" in said
          and "nothing was deleted" in said, str(said))
    check("partial: the failed directory and its file are intact",
          (skills / "n1" / "f").read_text() == "x" and not skills.is_symlink())


def test_a_refused_register_leaves_no_account_and_names_a_type_clash(home: Path) -> None:
    """The plan comes before the account directory and its vendor record, so a
    refusal leaves nothing behind; a file/directory clash says which is which."""
    os.environ["OCP_HOME"] = str(home)
    real = accounts._plan_item
    accounts._plan_item = lambda *a, **k: ("skip", ["CLAUDE.md: a real file that differs"], [])
    try:
        with second_vendor():
            for vendor in (None, "zai"):
                try:
                    accounts.register("fresh", force=True, vendor=vendor)
                    said = None
                except accounts.AdoptRefused as e:
                    said = str(e)
                check(f"refused ({vendor}): it says nothing was changed",
                      said is not None and "nothing was changed" in said, str(said))
                check(f"refused ({vendor}): no directory, no account row, no vendor record",
                      not paths.account_dir("fresh").exists()
                      and "fresh" not in paths.list_accounts())
    finally:
        accounts._plan_item = real

    accounts.register("gamma")
    shared = vendors.shared_dir("claude") / "skills"
    shared.mkdir(parents=True, exist_ok=True)
    link = vendors.config_dir("gamma") / "skills"
    link.unlink()
    (shared / "d").mkdir()
    (shared / "f").write_text("s")
    (link / "d").parent.mkdir(parents=True)
    (link / "d").write_text("a file here")
    (link / "f").mkdir()
    try:
        accounts.register("gamma", force=True)
        said = ""
    except accounts.AdoptRefused as e:
        said = str(e)
    check("type clash: a file here against a directory in shared is named as that",
          "d: a file here, a directory in shared" in said, said)
    check("type clash: a directory here against a file in shared is named as that",
          "f: a directory here, a file in shared" in said, said)


def test_agent_memory_budget_migration_and_team_layer(home: Path) -> None:
    """T231: memory is reported against its budget and never rewritten;
    migrate is a dry run until --apply and never touches shared/memory; the
    team layer is a file read per team."""
    import time as _t
    from ocp.team import memory
    from ocp.cli import memory as climemory
    os.environ["OCP_HOME"] = str(home)
    agents.register("orcapipe")
    agents.register("orcapipe.ui")
    mem = home / "agents" / "orcapipe" / "memory"
    check("register: the agent's memory dir exists", mem.is_dir())
    check("status: no index is not over budget", not memory.status("orcapipe")["over"])

    (mem / "MEMORY.md").write_text("- [a](a.md) - x\n" * 21)
    st = memory.status("orcapipe")
    check("status: 21 entries is over the 20 cap", st["entries"] == 21 and st["over"])
    (mem / "MEMORY.md").write_text("- [a](a.md) - " + "y" * 2100 + "\n")
    check("status: over 2KB is over budget", memory.status("orcapipe")["over"])
    (mem / "MEMORY.md").write_text("- [a](a.md) - x\n")
    (mem / "a.md").write_text("rule a")
    (mem / "b.md").write_text("rule b")
    now = _t.time()
    st = memory.status("orcapipe", now)
    check("status: a file the index does not name is listed", st["unindexed"] == ["b.md"])
    check("status: a file just written is not stale", st["stale"] == [])
    old = now - 29 * 86400
    (home / "agents" / "orcapipe" / "memory-use.json").write_text(
        json.dumps({"a.md": old, "b.md": now}))
    check("status: 28 days unused makes a candidate, a recent use does not",
          memory.status("orcapipe", now)["stale"] == ["a.md"])
    check("status: ocp never deletes it", (mem / "a.md").exists())
    said = _capture(lambda: climemory.cmd_memory_status(argparse.Namespace(agent="orcapipe")))
    check("status: the command prints the agent's line", said.startswith("orcapipe: ")
          and "not in the index: b.md" in said, said)

    (home / "agents" / "orcapipe.ui" / "memory").mkdir(exist_ok=True)
    (mem / "a.md").write_text("rule a")
    (home / "agents" / "orcapipe.ui" / "memory" / "same.md").write_text("Rule  a")
    check("promotion: the same text in two agents is a candidate",
          any(len(v) == 2 for _k, v in memory.promotion_candidates()))

    src = home / "shared" / "memory" / "claude" / "-Users-x-orcapipe"
    src.mkdir(parents=True)
    other = home / "shared" / "memory" / "claude" / "-Users-x-harbor-app"
    other.mkdir(parents=True)
    (other / "idle-not-free.md").write_text("another repo's fact")
    (src / "idle-not-free.md").write_text(
        "---\nname: idle-not-free\ndescription: do not route by idleness alone\n---\nbody")
    (src / "ocp-cli-habits.md").write_text("q")
    (src / "project-ocp-home-wipe.md").write_text("w")
    (src / "unknown.md").write_text("u")
    (src / "feedback-testing.md").write_text("t")
    rows = {r["file"].stem: r["action"] for r in memory.plan_migration()}
    check("migrate: each file's fate comes from the table",
          rows == {"idle-not-free": "copy", "ocp-cli-habits": "promote",
                   "project-ocp-home-wipe": "delete", "unknown": "no-owner",
                   "feedback-testing": "merge"}, str(rows))
    long_src = src / "long.md"
    long_src.write_text("---\nname: long\ndescription: " + "d" * 300 + "\n---\nb")
    line = memory._index_line(long_src)
    check("index line: a long hook is cut after the link, which stays whole",
          len(line) <= memory.INDEX_LINE and line.startswith("- [long](long.md) — d")
          and line.endswith("…"), line)
    long_src.write_text("---\nname: " + "n" * 200 + "\n---\nb")
    line = memory._index_line(long_src)
    check("index line: a long title is shortened, the link target is intact",
          len(line) <= memory.INDEX_LINE and line.endswith("](long.md)"), line)
    long_src.unlink()
    bad = []
    for fname in ("a.md", "topic.md", "x" * 40 + ".md", "x" * 89 + ".md", "x" * 90 + ".md",
                  "x" * 91 + ".md", "x" * 92 + ".md", "x" * 147 + ".md"):
        f = src / fname
        for nlen in range(0, 121):
            for hlen in (0, 1, 2, 3, 4, 50, 300):
                meta = "---\n" + (f"name: {'n' * nlen}\n" if nlen else "") \
                    + (f"description: {'d' * hlen}\n" if hlen else "") + "---\nb"
                f.write_text(meta)
                try:
                    line = memory._index_line(f)
                except ValueError:
                    if memory._fits_index(fname):
                        bad.append((fname, nlen, hlen, "raised"))
                    continue
                if (not memory._fits_index(fname) or len(line) > memory.INDEX_LINE
                        or not re.fullmatch(r"- \[[^\]]+\]\(" + re.escape(fname) + r"\)( — .+)?",
                                            line)):
                    bad.append((fname, nlen, hlen, line))
        f.unlink()
    check("index line: for every name, file name and hook length the line is within "
          "budget, the link whole, and a file name that cannot fit raises", not bad, str(bad[:3]))
    exact = src / "topic.md"
    exact.write_text("---\nname: " + "n" * 86 + "\ndescription: some words\n---\nb")
    line = memory._index_line(exact)
    check("index line: a link of exactly 100 characters keeps its closing paren",
          line == "- [" + "n" * 86 + "](topic.md)", line)
    exact.unlink()
    long_file = src / ("x" * 150 + ".md")
    long_file.write_text("x")
    memory.OWNERS[long_file.stem] = "orcapipe"
    try:
        acts = [r["action"] for r in memory.plan_migration() if r["file"] == long_file]
    finally:
        del memory.OWNERS[long_file.stem]
    check("migrate: a file name too long to be listed is reported, not copied",
          acts == ["long-name"], str(acts))
    long_file.unlink()
    promote =[r for r in memory.plan_migration() if r["file"].stem == "ocp-cli-habits"]
    check("migrate: a promoted file names AGENTS.md, apart from agent names",
          promote[0]["action"] == "promote" and promote[0]["to"] == "AGENTS.md"
          and not set(memory.OWNERS.values()) & {"AGENTS.md", "delete"})
    (home / "agents" / "orcapipe.ui" / "memory" / "a.md").write_text("rule a")
    said = _capture(lambda: climemory.cmd_memory_status(argparse.Namespace(agent="orcapipe.ui")))
    check("status: named for an agent, only its own duplicate groups are shown",
          "orcapipe/a.md" in said and "orcapipe.ui/a.md" in said, said)
    agents.register("orcapipe.auditor")
    (home / "agents" / "orcapipe.auditor" / "memory" / "z.md").write_text("only mine")
    lone = _capture(lambda: climemory.cmd_memory_status(argparse.Namespace(agent="orcapipe.auditor")))
    check("status: an agent in no duplicate group sees none", "same text" not in lone, lone)
    check("migrate: another repo's folder is not read",
          all(r["file"].parent == src for r in memory.plan_migration()))
    args = argparse.Namespace(apply=False)
    _capture(lambda: climemory.cmd_memory_migrate(args))
    check("migrate: a dry run writes nothing", not (mem / "idle-not-free.md").exists())
    args.apply = True
    _capture(lambda: climemory.cmd_memory_migrate(args))
    check("migrate --apply: copied verbatim, with an index line",
          (mem / "idle-not-free.md").read_text().endswith("body")
          and "[idle-not-free](idle-not-free.md)" in (mem / "MEMORY.md").read_text())
    check("migrate --apply: the shared folder is untouched",
          sorted(p.name for p in src.iterdir()) == sorted(
              ["idle-not-free.md", "ocp-cli-habits.md", "project-ocp-home-wipe.md",
               "unknown.md", "feedback-testing.md"]))
    n = (mem / "MEMORY.md").read_text().count("idle-not-free")
    _capture(lambda: climemory.cmd_memory_migrate(args))
    check("migrate --apply: a second run adds nothing",
          (mem / "MEMORY.md").read_text().count("idle-not-free") == n)

    teams = {"core": ["orcapipe", "orcapipe.ui"], "other": ["x"]}
    (home / "shared" / "team").mkdir(parents=True)
    (home / "shared" / "team" / "core.md").write_text("- one rule\n")
    check("team layer: read for a member, not for another team's",
          memory.team_layers("orcapipe", teams) == [("core", "- one rule")]
          and memory.team_layers("x", teams) == [])
    check("team layer: within budget is not a problem", not memory.team_problems(teams))
    (home / "shared" / "team" / "core.md").write_text("- r\n" * 11)
    check("team layer: 11 rules is over", len(memory.team_problems(teams)) == 1)


def test_team_rules_reach_the_prompt_and_doctor_reports_memory(home: Path) -> None:
    """T231: the team file is in the system prompt of a member only, an empty
    team in team.yaml breaks nothing, and doctor says what is over budget."""
    from ocp.session import launch
    _daemon_home(home, 20)
    hand_edit_team("""
teams:
  dev: [marina]
  empty:

agents:
  marina: {}
  scout: {}

edges:
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
    for n in ("marina", "scout"):
        agents.register(n)
    (home / "shared" / "team").mkdir(parents=True)
    (home / "shared" / "team" / "dev.md").write_text("- run the tests\n")
    (home / "shared" / "team" / "empty.md").write_text("- nobody reads this\n")
    ctx, other = launch.context_for("marina"), launch.context_for("scout")
    check("team rules: a member's prompt carries its team's file",
          "# Rules for team dev\n- run the tests" in ctx, ctx)
    check("team rules: another agent's does not, and an empty team is no error",
          "run the tests" not in other and "nobody reads this" not in ctx + other)

    def row(r):
        return [(lv, d) for lv, n, d in r.rows if n.startswith("memory")]
    r = doctor.Report()
    doctor._check_memory(r)
    check("doctor: memory within budget says OK", [lv for lv, _ in row(r)] == [doctor.OK], str(r.rows))
    mem = home / "agents" / "marina" / "memory"
    (mem / "MEMORY.md").write_text("- [a](a.md) - x\n" * 21)
    (mem / "loose.md").write_text("x")
    r = doctor.Report()
    doctor._check_memory(r)
    got = row(r)
    check("doctor: an over-budget, unindexed agent is a WARN naming it",
          got and got[0][0] == doctor.WARN and "marina: " in got[0][1]
          and "OVER BUDGET" in got[0][1] and "loose.md" in got[0][1], str(got))
    (mem / "MEMORY.md").write_text("- [loose](loose.md) - x\n")
    old = time.time() - 29 * 86400
    os.utime(mem / "loose.md", (old, old))
    r = doctor.Report()
    doctor._check_memory(r)
    check("doctor: a file unused for 28 days is a WARN", "unused 28+ days" in str(row(r)), str(row(r)))
    os.utime(mem / "loose.md")
    (home / "shared" / "team" / "dev.md").write_text("- r\n" * 11)
    r = doctor.Report()
    doctor._check_memory(r)
    check("doctor: an over-budget team file is a WARN", "team dev: " in str(row(r)), str(row(r)))


def test_the_opencode_driver(home: Path) -> None:
    """The opencode driver's answers, each from what 1.18.32 was measured to
    do (design/03 §4.3). Nothing here runs opencode: `auth list` is replaced
    by its measured output and the binary by a path."""
    os.environ["OCP_HOME"] = str(home)
    drv = vendors.get("opencode")
    drv.binary = lambda: "/x/bin/opencode"
    runs: list[tuple[list, dict]] = []
    listed = {"out": _auth_list_out(1)}

    def fake_run(argv, **kw):
        runs.append((argv, kw.get("env") or {}))
        if isinstance(listed["out"], Exception):
            raise listed["out"]
        return subprocess.CompletedProcess(argv, 0, listed["out"], "")

    real_run = subprocess.run
    try:
        accounts.register("alpha")
        accounts.register("zai", vendor="opencode")
        cdir = vendors.config_dir("zai")
        check("opencode: its account's directory is XDG_DATA_HOME's, made at register",
              cdir == paths.account_dir("zai") / "xdg-data" and cdir.is_dir(), str(cdir))
        # Launched from inside a claude agent, whose own variable names its
        # account. auditor's #1906: XDG_DATA_HOME is also the user's own, and
        # claude reads it, so only a value pointing into one of our accounts
        # is dropped.
        saved = {k: os.environ.get(k) for k in ("XDG_DATA_HOME", "CLAUDE_CONFIG_DIR")}
        try:
            os.environ["CLAUDE_CONFIG_DIR"] = str(vendors.config_dir("alpha"))
            env = accounts.env_for("zai")
            check("opencode: a session's environment names it in XDG_DATA_HOME",
                  env.get("XDG_DATA_HOME") == str(cdir) and "CLAUDE_CONFIG_DIR" not in env,
                  str((env.get("XDG_DATA_HOME"), env.get("CLAUDE_CONFIG_DIR"))))
            os.environ["XDG_DATA_HOME"] = str(home / "user-share")
            check("opencode: a claude launch keeps the XDG_DATA_HOME it inherits",
                  accounts.env_for("alpha").get("XDG_DATA_HOME") == str(home / "user-share"),
                  str(accounts.env_for("alpha").get("XDG_DATA_HOME")))
            os.environ["XDG_DATA_HOME"] = str(cdir)
            check("opencode: but not one naming an opencode account",
                  "XDG_DATA_HOME" not in accounts.env_for("alpha"),
                  str(accounts.env_for("alpha").get("XDG_DATA_HOME")))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        check("opencode: the login a human is handed is opencode's, under that directory",
              accounts.login_command("zai") == f'XDG_DATA_HOME="{cdir}" opencode auth login',
              accounts.login_command("zai"))
        check("opencode: the account is metered - the coding plan answers a usage endpoint",
              not status.read("zai").unmetered)

        # Logged in: the file first, then opencode's own count.
        subprocess.run = fake_run
        check("opencode: no auth.json is not logged in, and opencode is not asked",
              not accounts.logged_in("zai") and runs == [], str(runs))
        (cdir / "opencode").mkdir()
        (cdir / "opencode" / "auth.json").write_text("{}")
        check("opencode: one credential counted is logged in",
              accounts.logged_in("zai"), str(runs))
        check("opencode: and the count was asked of this account's directory",
              runs and runs[-1][0] == ["/x/bin/opencode", "auth", "list"]
              and runs[-1][1].get("XDG_DATA_HOME") == str(cdir), str(runs[-1:]))
        listed["out"] = _auth_list_out(0)
        check("opencode: the auth.json a logout leaves behind is not logged in",
              not accounts.logged_in("zai"))
        listed["out"] = subprocess.TimeoutExpired(["opencode"], 20)
        check("opencode: nor is one opencode cannot answer for",
              not accounts.logged_in("zai"))
        listed["out"] = _auth_list_out(1)

        # A session: the TUI's own command line, as `ps` shows it.
        tui = "/Users/ron/.opencode/bin/opencode -m zai-coding-plan/glm-5.2"
        for line, want in ((tui, True),
                           ("opencode", True),
                           ("/Users/ron/.opencode/bin/opencode -m m -s ses_1", True),
                           ("/Users/ron/.opencode/bin/opencode run fix it", False),
                           ("/Users/ron/.opencode/bin/opencode auth list", False),
                           ("/Users/ron/.opencode/bin/opencode serve", False),
                           (f"script -q /dev/null {tui}", False),
                           ("/usr/local/bin/claude --append-system-prompt see "
                            f"{tui}", False),
                           ("/opt/x/myopencode -m m", False)):
            check(f"opencode: is_session({line[:48]!r}) is {want}",
                  drv.is_session(line) is want)
        check("opencode: claude does not claim the TUI's line either",
              not vendors.get("claude").is_session(tui))

        for extra, want in ((["-s", "ses_1"], True), (["--session", "ses_1"], True),
                            (["--session=ses_1"], True), (["-c"], True),
                            (["--continue"], True), (["--fork"], False),
                            (["-m", "x"], False), (None, False)):
            check(f"opencode: is_resume({extra}) is {want}", drv.is_resume(extra) is want)

        agents.register("glm", model="zai-coding-plan/glm-5.2")
        _with_vendor("glm", "opencode")
        argv, env = launch.build("glm")
        check("opencode: a launch is the TUI with its model and nothing ocp cannot "
              "express in its argv",
              argv == ["/x/bin/opencode", "-m", "zai-coding-plan/glm-5.2"], str(argv))
        check("opencode: into the account's own data directory",
              env.get("XDG_DATA_HOME") == str(cdir) and env.get("OCP_AGENT") == "glm",
              str(env.get("XDG_DATA_HOME")))
        check("opencode: no model configured leaves the choice to opencode",
              drv.launch_argv("/x/bin/opencode", None, "high", "Agent", ["--mcp-config", "m"],
                              "a preamble") == ["/x/bin/opencode"])

        # No memory root: everything that walks one finds nothing on this
        # account, and still finds claude's.
        accounts.register("zai2", vendor="opencode")
        mem = paths.account_dir("alpha") / ".claude" / "projects" / "-tmp-a-b"
        (mem / "memory").mkdir(parents=True)
        (mem / "sess1.jsonl").write_text(_call_line("req_alpha", 10))
        check("opencode: a transcript is still found on claude's account past it",
              handoffmod.find_transcript("sess1", "zai") == mem / "sess1.jsonl",
              str(handoffmod.find_transcript("sess1", "zai")))
        found = collect.locate("/tmp/a/b")
        check("opencode: and the collector's search passes over it to claude's",
              found is not None and found.account == "alpha", str(found))
        check("opencode: a worktree move renames only claude's directory",
              rehome._project_dirs(Path("/tmp/a/b"), Path("/tmp/c"))
              == [(mem, mem.parent / "-tmp-c")],
              str(rehome._project_dirs(Path("/tmp/a/b"), Path("/tmp/c"))))
        from ocp.usage import tokenlog
        con = db.connect()
        try:
            check("opencode: the token log reads nothing for it",
                  tokenlog.ingest(con, "zai") == 0)
        finally:
            con.close()
    finally:
        subprocess.run = real_run
        del drv.binary


def test_a_clear_looks_the_vendor_up_once_and_refuses_in_its_own_words(home: Path) -> None:
    """T39 (auditor #1946): one clear asks for the terminal's driver once, not
    once per step, and the daemon's refusals name the vendor's own clear
    command - opencode's /new, not claude's /clear."""
    os.environ["OCP_HOME"] = str(home)
    real = {n: getattr(orcamod, n) for n in
            ("_lookup_driver", "_orca_idle", "_screen", "alive", "send", "available",
             "background_work", "screen_blocker", "close_session", "clear_context")}
    real_present = dmn_delivery.session_present
    con = db.connect()
    try:
        accounts.register("alpha")
        accounts.register("zai", vendor="opencode")
        agents.register("glm", model="zai-coding-plan/glm-5.3-flash")
        _with_vendor("glm", "opencode")
        agents.register("backend")
        db.set_terminal(con, "glm", "term_oc", "wt")
        db.set_terminal(con, "backend", "term_cl", "wt")

        # The suite stubs these; this test is about the real ones.
        for n in ("screen_blocker", "close_session", "clear_context"):
            setattr(orcamod, n, REAL_ORCA[n])
        looked: list[str] = []
        lookup = real["_lookup_driver"]
        orcamod._lookup_driver = lambda h: (looked.append(h), lookup(h))[1]
        sent: list[str] = []
        orcamod.alive = lambda h: True
        orcamod._orca_idle = lambda h, t: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        orcamod._screen = lambda h: _screen_of(OC_IDLE if h == "term_oc" else COMPOSER_EMPTY)
        orcamod.clear_context("term_oc", wait=False)
        check("clear: opencode is sent its own /new", sent == ["/new"], str(sent))
        check("clear: the driver was looked up once for the whole clear",
              looked == ["term_oc"], str(looked))
        looked.clear()
        orcamod.close_session("term_cl", wait=False)
        check("close: and once for a whole close", looked == ["term_cl"], str(looked))

        orcamod.available = lambda: True
        dmn_delivery.session_present = lambda agent: False
        _, why = dmn_delivery._reclaim_context(con, "glm", lambda _m: None)
        check("refusal: a shell prompt under opencode names /new", "/new" in why and "/clear" not in why, why)
        _, why = dmn_delivery._reclaim_context(con, "backend", lambda _m: None)
        check("refusal: and under claude still names /clear", "/clear" in why, why)
        dmn_delivery.session_present = lambda agent: True
        orcamod.background_work = lambda h: ["a job"]
        _, why = dmn_delivery._reclaim_context(con, "glm", lambda _m: None)
        check("refusal: background work under opencode names /new too",
              "/new" in why and "/clear" not in why, why)
    finally:
        for n, v in real.items():
            setattr(orcamod, n, v)
        dmn_delivery.session_present = real_present
        con.close()


def test_an_opencode_session_under_ocp(home: Path) -> None:
    """#1926, each part measured on opencode 1.18.32 in an Orca terminal: the
    preamble reaches the session as an instructions file, the start prompt
    as --prompt, Agent's denial as opencode's task permission; /new and
    /exit are its commands; and since Orca's tui-idle reads an opencode turn
    as idle throughout, idle and what is in the way come from its screen."""
    os.environ["OCP_HOME"] = str(home)
    drv = vendors.get("opencode")
    drv.binary = lambda: "/x/bin/opencode"
    drv.credential_present = lambda config_dir: True
    real = {n: getattr(orcamod, n) for n in ("_run", "_screen", "alive", "send", "SCREEN_POLL_S")}
    real_execve, real_record = os.execve, launch._record_terminal
    try:
        accounts.register("alpha")
        accounts.register("zai", vendor="opencode")
        agents.register("glm", model="zai-coding-plan/glm-5.3-flash")
        _with_vendor("glm", "opencode")
        agents.register("backend")
        (vendors.config_dir("alpha") / ".credentials.json").write_text("{}")
        paths.agent_dir("glm").joinpath("ROLE.md").write_text("Build the thing.\n")

        # The start prompt: the preamble in a file opencode is pointed at,
        # Agent denied as its task tool, and nothing of either in the argv.
        argv, env = launch.build("glm")
        cfg = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
        pre = paths.agent_dir("glm") / "opencode-preamble.md"
        check("opencode session: the preamble is an instructions file of its own",
              cfg.get("instructions") == [str(pre)] and pre.exists()
              and pre.read_text() == launch.context_for("glm"), str(cfg))
        check("opencode session: and carries the role",
              "Build the thing." in pre.read_text())
        check("opencode session: Agent is denied as opencode's task tool, and nothing else",
              cfg.get("permission") == {"task": "deny"}, str(cfg.get("permission")))
        check("opencode session: the argv carries neither",
              argv == ["/x/bin/opencode", "-m", "zai-coding-plan/glm-5.3-flash"], str(argv))
        check("opencode session: a claude launch gets no opencode config",
              "OPENCODE_CONFIG_CONTENT" not in launch.build("backend")[1])

        ran: list[list[str]] = []
        os.execve = lambda exe, a, e: ran.append(list(a))
        launch._record_terminal = lambda *a, **k: None
        for agent in ("glm", "backend"):
            f = home / f"prompt-{agent}"
            f.write_text("Your first task.")
            launch.exec_agent(agent, prompt_file=str(f))
        check("opencode session: the start prompt is submitted with --prompt, not as the "
              "project directory", ran[0][-2:] == ["--prompt", "Your first task."], str(ran[0]))
        check("opencode session: claude's is still its positional",
              ran[1][-1] == "Your first task." and "--prompt" not in ran[1], str(ran[1]))

        con = db.connect()
        try:
            db.set_terminal(con, "glm", "term_oc", None)
            db.set_terminal(con, "backend", "term_cl", None)
        finally:
            con.close()
        check("opencode session: a terminal is read as its agent's vendor",
              [orcamod._driver_for(h).name for h in ("term_oc", "term_cl", "term_nobody")]
              == ["opencode", "claude", "claude"])

        # What is in the way, read off opencode's own screen.
        orcamod.screen_blocker = REAL_ORCA["screen_blocker"]
        shown = {"tail": OC_IDLE}
        orcamod._screen = lambda h: _screen_of(shown["tail"])
        for name, tail, want in (("idle", OC_IDLE, None), ("start", OC_HOME, None),
                                 ("working", OC_WORKING, None),
                                 ("permission", OC_PERMISSION, orcamod.MODAL),
                                 ("no prompt box", ["loading"], orcamod.UNREADABLE)):
            shown["tail"] = tail
            b = orcamod.screen_blocker("term_oc")
            check(f"opencode session: its {name} screen has {want or 'nothing'} in the way",
                  (b.kind if b else None) == want, str(b))
        shown["tail"] = OC_PERMISSION
        check("opencode session: the permission request is quoted",
              orcamod.screen_blocker("term_oc").detail
              == "△ Permission required / ← Access external directory /etc / Patterns",
              orcamod.screen_blocker("term_oc").detail)
        shown["tail"] = OC_IDLE
        check("opencode session: claude's reading of the same screen is unchanged",
              orcamod.screen_blocker("term_cl").kind == orcamod.UNREADABLE)

        # Idle: Orca says yes throughout an opencode turn, so the screen decides.
        orcamod.SCREEN_POLL_S = 0
        orca_says = {"satisfied": True}
        waits: list[str] = []

        def fake_run(args, timeout=180):
            waits.append(args[0])
            return True, json.dumps({"result": {"wait": {"satisfied": orca_says["satisfied"]}}})
        orcamod._run = fake_run
        reads = iter([OC_WORKING, OC_WORKING, OC_IDLE])
        orcamod._screen = lambda h: _screen_of(next(reads))
        check("opencode session: idle once the screen stops offering an interrupt",
              orcamod.wait_idle("term_oc", 5000) and next(reads, "spent") == "spent")
        orcamod._screen = lambda h: _screen_of(OC_WORKING)
        check("opencode session: a turn still running is not idle, whatever Orca says",
              not orcamod.wait_idle("term_oc", 30))
        orcamod._screen = lambda h: _screen_of(OC_HOME)
        check("opencode session: its start screen is idle", orcamod.wait_idle("term_oc", 30))
        orcamod._screen = lambda h: _screen_of(OC_HOME[:1])
        check("opencode session: a screen still booting, no prompt box yet, is not",
              not orcamod.wait_idle("term_oc", 30))
        orca_says["satisfied"] = False
        orcamod._screen = lambda h: _screen_of(OC_HOME)
        check("opencode session: Orca's no stands over an idle screen",
              not orcamod.wait_idle("term_oc", 30))
        orca_says["satisfied"] = True
        orcamod._screen = lambda h: (_ for _ in ()).throw(AssertionError("claude screen read"))
        check("opencode session: a claude terminal is still Orca's word alone",
              orcamod.wait_idle("term_cl", 30))

        # /new and /exit, typed only where nothing is in the way.
        sent: list[tuple[str, str]] = []
        orcamod.alive = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append((h, text)), (True, ""))[1]
        orcamod._screen = lambda h: _screen_of(OC_IDLE if h == "term_oc" else COMPOSER_EMPTY)
        for h in ("term_oc", "term_cl"):
            orcamod.clear_context(h, wait=False)
            orcamod.close_session(h, wait=False)
        check("opencode session: its clear is /new and its exit /exit; claude's are unchanged",
              sent == [("term_oc", "/new"), ("term_oc", "/exit"),
                       ("term_cl", "/clear"), ("term_cl", "/exit")], str(sent))
        orcamod._screen = lambda h: _screen_of(OC_PERMISSION)
        sent.clear()
        ok, why = orcamod.clear_context("term_oc", wait=False)
        check("opencode session: nothing is typed at a permission request",
              not ok and sent == [] and "not typing /new" in why, why)
    finally:
        for n, f in real.items():
            setattr(orcamod, n, f)
        os.execve, launch._record_terminal = real_execve, real_record
        del drv.binary, drv.credential_present


def test_an_opencode_row_takes_its_state_word_from_orca(home: Path) -> None:
    """#59: claude agents report RUNNING and IDLE through hooks; opencode has
    no counterpart wired, so production writes IDLE at launch and the row
    sits there for the life of the session - an agent working right now reads
    IDLE. The state cell reads the pane's screen instead (tui-idle answers
    idle mid-turn for opencode, measured), and only on positive evidence both
    ways: the prompt box with "interrupt" in its footer is working, the box
    without it is idle, and anything else - a closed agent's shell prompt, a
    start screen - is no evidence, so the db state stands. A closed row is
    never screened at all. A claude row is never screened: its hooks already
    said."""
    use_home(home)
    accounts.register("alpha")
    accounts.register("zai", vendor="opencode")
    agents.register("backend")
    agents.register("glm")
    _with_vendor("glm", "opencode")
    con = db.connect()
    screens = {"term_oc": OC_WORKING}
    asked: list[list] = []
    real_screen = orcamod._screen
    orcamod._screen = lambda h: (asked.append([h]), {
        "source": "screen", "tail": screens[h]})[1]
    saved_seen = dict(tui._opencode_seen)
    tui._opencode_seen.clear()

    def row(agent):
        return db.get_agent_state(con, agent)

    try:
        db.set_agent_state(con, "backend", dmn_states.RUNNING)
        db.set_terminal(con, "backend", "term_cl", "wt")
        db.set_account(con, "backend", "alpha")
        # What production writes for an opencode agent: IDLE.
        db.set_agent_state(con, "glm", dmn_states.IDLE)
        db.set_terminal(con, "glm", "term_oc", "wt")
        db.set_account(con, "glm", "zai")

        word, detail = tui.screen_state(con, row("glm"))
        check("opencode row: a screen mid-turn reads RUNNING over the db's IDLE",
              (word, detail) == ("RUNNING", ""), f"{word!r} {detail!r}")
        check("opencode row: the claude row is never screened",
              asked == [["term_oc"]], str(asked))

        screens["term_oc"] = OC_IDLE
        tui._opencode_seen.clear()
        check("opencode row: an idle screen reads IDLE",
              tui.screen_state(con, row("glm"))[0] == "IDLE")

        # And the other direction: a RUNNING row over an idle screen.
        db.set_agent_state(con, "glm", dmn_states.RUNNING)
        check("opencode row: an idle screen keeps a RUNNING row honest",
              tui.screen_state(con, row("glm"))[0] == "IDLE")

        # A shell prompt is evidence of neither: the db word stands. This is
        # what a closed agent's pane shows (auditor #2157, measured live).
        shell = ["Resume this session with:",
                 "claude --resume 9f2289d7-3b45-4e86-9653-efb48c2d46da",
                 "ron@Ron-MacBookAir  ~/orca/workspaces/orcapipe/orcapipe.glm "]
        db.set_agent_state(con, "glm", dmn_states.IDLE)
        con.execute("UPDATE agent_state SET closed_at = ? WHERE agent = 'glm'",
                    (int(time.time()),))
        con.commit()
        screens["term_oc"] = shell
        tui._opencode_seen.clear()
        asked.clear()
        check("opencode row: a closed agent's shell prompt is not work in progress",
              tui.screen_state(con, row("glm"))[0] == "IDLE" and asked == [],
              str(asked))

        # A closed row is never screened at all, whatever its pane shows.
        asked.clear()
        screens["term_oc"] = OC_WORKING
        tui._opencode_seen.clear()
        tui.screen_state(con, row("glm"))
        check("opencode row: a closed row is never screened",
              asked == [], str(asked))
        con.execute("UPDATE agent_state SET closed_at = NULL WHERE agent = 'glm'")
        con.commit()

        orcamod._screen = lambda h: {"source": "stream", "tail": OC_WORKING}
        tui._opencode_seen.clear()
        check("opencode row: an unreadable screen leaves the db state standing",
              tui.screen_state(con, row("glm"))[0] == "IDLE")

        # The tri-state, at the driver: box + interrupt is working, box
        # without it is idle, no box is no evidence.
        drv = vendors.get("opencode")
        check("opencode row: the driver's screen answer is three-valued",
              drv.working_on_screen(OC_WORKING) is True
              and drv.working_on_screen(OC_IDLE) is False
              and drv.working_on_screen(shell) is None,
              str((drv.working_on_screen(OC_WORKING),
                   drv.working_on_screen(OC_IDLE),
                   drv.working_on_screen(shell))))

        orcamod._screen = real_screen
        tui._opencode_seen.clear()
        asked.clear()
        orcamod._screen = lambda h: (asked.append([h]), {
            "source": "screen", "tail": OC_WORKING})[1]
        first = tui.screen_state(con, row("glm"))[0]
        second = tui.screen_state(con, row("glm"))[0]
        check("opencode row: the cache holds within the ttl - one read per pane",
              first == second == "RUNNING" and asked == [["term_oc"]], str(asked))
    finally:
        orcamod._screen = real_screen
        tui._opencode_seen = saved_seen
        con.close()


def test_shared_account_context_isolation(home: Path) -> None:
    """Two agents on one account must not be judged by each other's context.

    Rate limits are account-wide and shared correctly; context is per session,
    and each agent's comes from the transcript its own session is writing.
    """
    use_home(home)
    agents.register("ag_a")
    agents.register("ag_b")
    accounts.register("shared")
    now = int(time.time())
    write_status(home, "shared", pct_5h=30, pct_7d=12, reset_5h=now + 1800,
                 rl_at=now, reset_7d=now + 99999, rl7_at=now)

    for agent, tokens in (("ag_a", 700_000), ("ag_b", 50_000)):
        cwd = home / f"wt-{agent}"
        cwd.mkdir(parents=True, exist_ok=True)
        write_transcript(home, "shared", cwd,
                         f"0000000{agent[-1]}-0000-4000-8000-00000000000a",
                         turns=[("claude-opus-5", {"cache_read_input_tokens": tokens})])
        collect.collect(agent, cwd)

    check("shared account: each agent writes its own status file",
          (home / "agents" / "ag_a" / "status.json").exists()
          and (home / "agents" / "ag_b" / "status.json").exists())
    a = status.read_for_agent("ag_a", "shared")
    b = status.read_for_agent("ag_b", "shared")
    check("shared account: each agent keeps its own context",
          (a.ctx_pct, b.ctx_pct) == (70, 5), f"a={a.ctx_pct} b={b.ctx_pct}")
    check("shared account: rate limits are still shared",
          a.short.pct == b.short.pct == 30, f"a={a.short.pct} b={b.short.pct}")

    # The context trigger must fire for the full agent and not for the other.
    ta = triggers.evaluate(a.short.pct, a.ctx_used)
    tb = triggers.evaluate(b.short.pct, b.ctx_used)
    check("shared account: the full agent's context trigger fires",
          ta.fired and ta.kind == "context", ta.reason)
    check("shared account: the other agent's does not", not tb.fired, tb.reason)

    # An agent with no session file of its own must not borrow one.
    agents.register("ag_c")
    c = status.read_for_agent("ag_c", "shared")
    check("shared account: an agent with no session file falls back to the account",
          c.short.pct == 30)


def test_agent_rewrite_uses_parser(home: Path) -> None:
    use_home(home)
    agents.register("rw", model="opus")
    # A spacing variant that prefix-matching would have duplicated.
    agents.agent_file("rw").write_text("name: rw\nmodel : opus\n")
    agents.register("rw", effort="high")
    cfg = agents.read("rw")
    check("agents: re-register goes through the parser", cfg.get("effort") == "high", str(cfg))
    text = agents.agent_file("rw").read_text()
    check("agents: no duplicate model key is left behind", text.count("model") == 1, text)
    check("agents: other fields survive the rewrite", cfg.get("model") == "opus", str(cfg))


def test_hr_unlink_says_when_a_wildcard_still_allows_it_and_link_wants_team_yaml(home: Path) -> None:
    """T34: removing an exact edge is not taking the permission away while a
    wildcard routes the pair - the result and the escalation say which edge.
    T35: an end that is registered but not in team.yaml is refused up front."""
    _daemon_home(home, 20)
    for n in ("marina", "scout", "loose"):
        agents.register(n)
    hand_edit_team("""agents:
  marina: {}
  scout: {}
edges:
  - { from: "marina", to: "scout", response: async }
  - { from: "*", to: "scout", relation: peer, response: async }
""")
    esc = hr._escalation({"kind": "unlink", "agent": "marina", "to": "scout", "id": 1,
                          "requested_by": "marina", "why": "w"})
    check("unlink: the escalation names the edge that still lets it through",
          "can still ask scout" in esc and "* -> scout" in esc, esc)
    ok, said = hr.enact("unlink", "marina", to="scout", why="w")
    check("unlink: it is removed, and the result says the wildcard still allows it",
          ok and "can still ask scout" in said and "* -> scout" in said, said)
    ok, said = hr.enact("link", "marina", to="loose", why="w")
    check("link: an agent registered but not in team.yaml is refused up front",
          not ok and "not in team.yaml" in said and "loose" in said, said)
    ok, said = hr.enact("link", "loose", to="scout", why="w")
    check("link: and so is the asking end", not ok and "not in team.yaml" in said, said)


def test_hr_team_is_one_proposal_and_a_team_has_default_links(home: Path) -> None:
    """T263: a manager and its members are proposed and approved once, and the
    ordinary links (`team.DEFAULT_LINKS`) need no edge; an explicit deny still wins."""
    _daemon_home(home, 20)
    original = """# routing
teams:
  orca: [orca]
agents:
  orca: {}
edges:
  - { from: "orca", to: "orca.*", relation: supervisor, response: async }
deny:
  - { from: "orca.*", to: "boat.rude", reason: "never" }
"""
    hand_edit_team(original)
    agents.register("orca")
    tf = home / "team.yaml"
    con = db.connect()
    try:
        ok, said = hr.propose("team", "boat", requester="orca", why="a new team",
                              members=["sea", "shore", "reviewer"], repos={"boat.sea": "/w/sea"},
                              repo="/w/boat")
        check("team: an agent proposes it, one proposal, nothing written yet",
              ok and "boat:" not in tf.read_text() and not agents.agent_file("boat").exists(), said)
        pid = said.split()[1]
        prop = hr.load_proposal(pid)
        body = db.get_request(con, prop["request"])["body"]
        check("team: the founder reads every agent, every repo and the default links",
              all(x in body for x in ("boat.sea", "boat.shore", "boat.reviewer", "/w/sea", "/w/boat"))
              and "may ask each other" in body and "approve" in body, body)
        check("team: one escalation for the whole team",
              con.execute("SELECT COUNT(*) c FROM requests WHERE target = 'human'").fetchone()["c"] == 1)
        ok, said = hr.apply(pid, con)
        check("team: refused until the founder answers", not ok, said)
        decisions.record("hr", pid, prop["request"], "approved", hr.subject_of(prop))
        con.execute("UPDATE requests SET state = 'done', verdict = 'approve' WHERE id = ?", (prop["request"],))
        for label, key, value in [("a member added", "members", prop["members"] + ",boat.spy"),
                                  ("a member's repo changed", "repo.boat.sea", "/elsewhere"),
                                  ("the manager's repo changed", "repo", "/elsewhere")]:
            edited = dict(prop)
            edited[key] = value
            hr.save(edited)
            ok, said = hr.apply(pid, con)
            check(f"team: {label} after the approval is refused", not ok
                  and "boat.spy" not in team.Team.load().agents, said)
        hr.save(prop)
        ok, said = hr.apply(pid, con)
        check("team: one approval creates the team", ok, said)
    finally:
        con.close()
    t = team.Team.load()
    check("team: the team and every agent are in team.yaml, with one supervisor edge",
          t.teams.get("boat") == ["boat", "boat.sea", "boat.shore", "boat.reviewer"]
          and all(n in t.agents for n in ("boat", "boat.sea", "boat.shore", "boat.reviewer"))
          and sum(1 for e in t.edges if e["from"] == "boat") == 1
          and "# routing" in tf.read_text() and original.splitlines()[-4] in tf.read_text())
    check("team: every agent is registered, with its own repo",
          all(n in paths.list_agents() for n in ("boat", "boat.sea", "boat.shore", "boat.reviewer"))
          and agents.read("boat.sea").get("repo") == "/w/sea"
          and agents.read("boat").get("repo") == "/w/boat"
          and agents.read("boat.shore").get("repo") in (None, ""))
    check("team: the manager is boat's supervisor",
          actions.supervisor_of(t, "boat.sea") == "boat")
    ok, said = hr.enact("team", "boat", why="again", members=["x"])
    check("team: a team that exists is refused", not ok and "already" in said, said)
    ok, said = hr.enact("team", "solo", why="w", members=[])
    check("team: no members is refused", not ok and "at least one" in said, said)
    ok, said = hr.enact("team", "solo", why="w", members=["a", "a"])
    check("team: a member twice is refused", not ok and "twice" in said, said)
    ok, said = hr.enact("team", "solo", why="w", members=["a"], repos={"ghost": "/x"})
    check("team: a repo for a stranger is refused", not ok and "ghost" in said, said)

    data = {"teams": {"boat": ["boat", "boat.sea", "boat.reviewer", "boat.shore"],
                      "orca": ["orca", "orca.core", "orca.reviewer"]},
            "agents": {}, "edges": [],
            "deny": [{"from": "boat.*", "to": "orca.reviewer", "reason": "no"}]}
    tm = team.Team(data)
    for who, whom, want in [
            ("boat", "boat.sea", True), ("boat.sea", "boat", True),
            ("boat.sea", "boat.reviewer", True), ("boat", "human", True),
            ("boat", "orca", True), ("orca", "boat", True),
            ("boat.sea", "boat.shore", False), ("boat.sea", "human", False),
            ("boat.sea", "orca", False), ("boat.sea", "orca.reviewer", False),
            ("boat.sea", "orca.core", False), ("boat", "orca.core", False)]:
        d = tm.route(who, whom)
        check(f"default links: {who} -> {whom} is {'allowed' if want else 'denied'}",
              d.allowed == want, d.reason)
    d = tm.route("boat", "human")
    check("default links: the founder is asked as an escalation", d.kind == "escalate" and d.creates_gate)
    check("default links: an explicit deny still wins",
          not tm.route("boat.sea", "orca.reviewer").allowed and "denied: " in tm.route("boat.sea", "orca.reviewer").reason)
    check("default links: an explicit edge still comes first",
          team.Team({**data, "edges": [{"from": "boat.sea", "to": "boat", "response": "fire_and_forget"}]})
          .route("boat.sea", "boat").response == "fire_and_forget")
    rc, out = _cli(["hr", "team", "reef", "--member", "dive=/w/dive", "--member", "reef.map",
                    "--repo", "/w/reef", "--why", "cli path"], as_agent=askmod.HUMAN)
    check("team: the founder's `hr team` creates it directly, suffix or full names, repo after '='",
          rc == 0 and team.Team.load().teams.get("reef") == ["reef", "reef.dive", "reef.map"]
          and agents.read("reef.dive").get("repo") == "/w/dive", out)
    rc, out = _cli(["hr", "team", "reef2", "--member", "a", "--why", "w"], as_agent="orca")
    check("team: an agent's `hr team` is a proposal", rc == 0 and "raised as" in out
          and "reef2" not in paths.list_agents(), out)
    from ocp.team import guide
    check("guide: the rule is written down once, in the team topic",
          all(d in guide.TOPICS["team"] for d in team.DEFAULT_LINKS))


def test_an_approve_on_a_team_proposal_is_recorded_and_applied(home: Path) -> None:
    """T273: raise, the founder answers approve, it is recorded and applied. And an
    answer given to a process that did not know the proposal kind (an older daemon
    or bridge) is closed unapplied; the founder's `confirm_answer` records and applies it,
    an agent's session cannot, and a draft changed since is refused."""
    _daemon_home(home, 20)
    hand_edit_team("""
teams:
  orca: [orca]
agents:
  orca: {}
edges:
  - { from: "orca", to: "orca.*", relation: supervisor, response: async }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
    agents.register("orca")
    con = db.connect()
    try:
        ok, said = hr.propose("team", "crew", requester="orca", why="a crew",
                              members=["a", "b"], repos={"crew.a": "/w/a"})
        pid = said.split()[1]
        prop = hr.load_proposal(pid)
        ok, said = askmod.resolve(prop["request"], "approve")
        check("team answer: the approve is applied by the answer itself", ok and "applied" in said, said)
        check("team answer: it is recorded, names the request, and the team exists",
              (decisions.get("hr", pid) or {}).get("request") == prop["request"]
              and hr.load_proposal(pid)["state"] == "applied"
              and team.Team.load().teams.get("crew") == ["crew", "crew.a", "crew.b"]
              and "crew.b" in paths.list_agents())

        ok, said = hr.propose("team", "old", requester="orca", why="an older process answers",
                              members=["x"])
        pid = said.split()[1]
        prop = hr.load_proposal(pid)
        kinds = hr.KINDS
        hr.KINDS = tuple(k for k in kinds if k != "team")
        try:
            ok, said = askmod.resolve(prop["request"], "approve")
            ok2, said2 = askmod.resolve(prop["request"], "reject")
            still_queued = db.get_request(con, prop["request"])["state"] == "queued"
        finally:
            hr.KINDS = kinds
        check("stale: a process that does not know the kind refuses the answer and says to restart",
              not ok and not ok2 and "does not know" in said and "Restart" in said, said)
        check("stale: the escalation is still queued, nothing recorded or applied",
              still_queued and hr.load_proposal(pid)["state"] == "proposed"
              and decisions.get("hr", pid) is None and "old" not in paths.list_agents())
        # What such a process used to do: close the row, unapplied.
        db.close_if_queued(con, prop["request"], "done", "approve")
        ok, said = hr.apply(pid, con)
        check("stale: apply refuses, as the report said", not ok and "no answer from the founder" in said, said)
        os.environ["OCP_AGENT"] = "orca"
        try:
            ok, said = hr.confirm_answer(pid, con)
            rc, out = _cli(["hr", "apply", pid, "--confirm-answer", "--yes"], as_agent="orca")
        finally:
            os.environ.pop("OCP_AGENT", None)
        check("stale: an agent's session cannot record it", not ok and "founder" in said
              and rc == 1 and decisions.get("hr", pid) is None, said + out)
        edited = dict(hr.load_proposal(pid))
        edited["members"] = "old.x,old.spy"
        hr.save(edited)
        ok, said = hr.confirm_answer(pid, con)
        check("stale: a draft changed since the answer is refused", not ok and "raise it again" in said, said)
        hr.save(prop)
        rc, out = _cli(["hr", "apply", pid, "--confirm-answer", "--yes"], as_agent=askmod.HUMAN)
        check("stale: the founder's own session records and applies it",
              rc == 0 and hr.load_proposal(pid)["state"] == "applied"
              and "old.x" in paths.list_agents(), out)
        ok, said = hr.propose("team", "open", requester="orca", why="not answered", members=["y"])
        ok, said = hr.confirm_answer(said.split()[1], con)
        check("stale: a proposal the founder has not answered cannot be confirmed",
              not ok and "not answered" in said, said)
    finally:
        con.close()


def test_hr_link_and_unlink_edit_one_edge_and_nothing_else(home: Path) -> None:
    """An ask edge is added or removed through hr, as text so the file's comments
    survive, validated before it is written, and gated like any roster change."""
    _daemon_home(home, 20)
    for n in ("marina", "keeper", "scout"):
        agents.register(n)
    original = """# routing - the reasons are in the comments
agents:
  marina: {}
  keeper: {}
  scout: {}
edges:
  - { from: "scout", to: "keeper", response: async }   # scout may ask keeper
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
  - { from: "*", to: "scout", relation: peer, response: async }
deny:
  - { from: "keeper", to: "scout", reason: "keeper never asks scout" }
"""
    tf = home / "team.yaml"
    hand_edit_team(original)
    con = db.connect()
    try:
        ok, said = hr.enact("link", "marina", to="keeper", why="marina needs keeper's review",
                            relation="peer", response="fire_and_forget")
        check("link: the founder adds an edge directly", ok, said)
        text = tf.read_text()
        check("link: the edge is one line with what was asked for",
              '  - { from: "marina", to: "keeper", relation: peer, response: fire_and_forget }'
              in text, text)
        check("link: it goes before the first wildcard edge, so it is reached",
              text.index('from: "marina"') < text.index('from: "*"'), text)
        check("link: the comments and every other line are kept",
              "# routing - the reasons are in the comments" in text
              and "# scout may ask keeper" in text
              and all(l in text for l in original.splitlines()), text)
        t = team.Team.load()
        d = t.route("marina", "keeper")
        check("link: the router now allows it, with the response given",
              d.allowed and d.response == "fire_and_forget", d.reason)

        for label, args, kw, want in [
                ("the same edge twice", ("marina", "keeper"), {}, "already has the edge"),
                ("a target that is not an agent", ("marina", "ghost"), {}, "not an agent"),
                ("itself", ("marina", "marina"), {}, "itself"),
                ("a route a deny covers", ("keeper", "scout"), {}, "deny"),
                ("a bad response", ("marina", "scout"), {"response": "blocking"}, "response"),
                ("a relation that is not a word", ("marina", "scout"),
                 {"relation": "a, b: c"}, "relation")]:
            ok, said = hr.enact("link", args[0], to=args[1], why="w", **kw)
            check(f"link: refused - {label}", not ok and want in said, said)
        check("link: and a refusal leaves the file as it was", tf.read_text() == text)

        ok, said = hr.enact("unlink", "marina", to="keeper", why="done with it")
        check("unlink: the founder removes it", ok, said)
        check("unlink: the file is back to what it was, comments and all",
              tf.read_text() == original, tf.read_text())
        ok, said = hr.enact("unlink", "marina", to="keeper", why="again")
        check("unlink: an edge that is not there is refused", not ok and "no edge" in said, said)
        ok, said = hr.enact("unlink", "marina", to="scout", why="w")
        check("unlink: a wildcard's coverage is not an edge to remove", not ok, said)

        ok, said = hr.propose("link", "marina", requester="marina", to="keeper", why="need it")
        check("link: an agent's own request is a proposal, nothing written yet",
              ok and 'from: "marina", to: "keeper"' not in tf.read_text(), said)
        pid = said.split()[1]
        prop = hr.load_proposal(pid)
        req = db.get_request(con, prop["request"])
        check("link: the founder is told which edge and how to decide",
              "marina -> keeper" in req["body"] and "approve" in req["body"], req["body"])
        ok, said = hr.apply(pid, con)
        check("link: apply refuses until the founder has answered", not ok, said)
        con.execute("UPDATE requests SET state = 'done', verdict = 'approve' WHERE id = ?",
                    (prop["request"],))
        ok, said = hr.apply(pid, con)
        check("link: a verdict written into the db alone, with no recorded answer, applies nothing",
              not ok and "no answer from the founder is on record" in said, said)
        decisions.record("hr", pid, prop["request"], "approved", hr.subject_of(hr.load_proposal(pid)))
        ok, said = hr.apply(pid, con)
        check("link: apply after approval adds the edge",
              ok and team.Team.load().route("marina", "keeper").allowed, said)

        agents.register("late")
        ok, said = hr.propose("unlink", "late", requester="late", to="keeper", why="x")
        check("unlink: refused at proposal time when there is no such edge", not ok, said)
        rc, out = _cli(["hr", "link", "keeper", "marina", "--why", "w", "--response",
                        "async"], as_agent=askmod.HUMAN)
        check("link: through the cli, as the founder, it is enacted",
              rc == 0 and team.Team.load().route("keeper", "marina").allowed, out)
        rc, out = _cli(["hr", "unlink", "keeper", "marina", "--why", "w"], as_agent=askmod.HUMAN)
        check("unlink: and removed", rc == 0 and not any(
            str(e.get("from")) == "keeper" and str(e.get("to")) == "marina"
            for e in team.Team.load().edges), out)
    finally:
        con.close()


def test_hr_add_writes_the_vendor_it_was_given(home: Path) -> None:
    """`hr add` writes `vendor:` into agent.yaml only when --vendor names a
    non-default one, both when the founder adds and when a proposal is applied."""
    _daemon_home(home, 20)
    accounts.register("alpha")
    accounts.register("zai", vendor="opencode")
    con = db.connect()
    try:
        ok, said = hr.enact("add", "claudey", why="w")
        check("hr add: succeeds with no vendor", ok, said)
        cfg = yamlcompat.load(agents.agent_file("claudey").read_text())
        check("hr add: no vendor key for the default vendor, same as every "
              "existing agent.yaml", "vendor" not in cfg, str(cfg))
        check("hr add: agent_vendor reads it as claude",
              vendors.agent_vendor("claudey") == "claude")

        ok, said = hr.enact("add", "glm", why="w", vendor="opencode")
        check("hr add: succeeds with --vendor opencode", ok, said)
        cfg = yamlcompat.load(agents.agent_file("glm").read_text())
        check("hr add: the vendor is written", cfg.get("vendor") == "opencode", str(cfg))
        check("hr add: agent_vendor reads it as opencode",
              vendors.agent_vendor("glm") == "opencode")
        check("hr add: no account is written", "account" not in cfg, str(cfg))

        hand_edit_team("""
agents:
  marina: {}
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
        agents.register("marina")
        ok, said = hr.propose("add", "glm2", requester="marina", why="w", vendor="opencode")
        check("hr add: a proposal with a vendor raises", ok, said)
        pid = said.split()[1]
        prop = hr.load_proposal(pid)
        check("hr add: the proposal itself records the vendor",
              prop.get("vendor") == "opencode", str(prop))
        req = db.get_request(con, prop["request"])
        check("hr add: the escalation names the vendor to the founder",
              "vendor opencode" in req["body"], req["body"])
        con.execute("UPDATE requests SET state = 'done', verdict = 'approve' WHERE id = ?",
                   (prop["request"],))
        decisions.record("hr", pid, prop["request"], "approved", hr.subject_of(hr.load_proposal(pid)))
        ok, said = hr.apply(pid, con)
        check("hr add: applying an approved proposal succeeds", ok, said)
        cfg = yamlcompat.load(agents.agent_file("glm2").read_text())
        check("hr add: apply writes the vendor the proposal recorded",
              cfg.get("vendor") == "opencode", str(cfg))

        # T182: the founder's answer is for the draft as they were shown it.
        # An approve whose first apply did not go through leaves the proposal
        # open, and a draft an agent edits before the retry is not what was
        # approved.
        ok, said = hr.propose("add", "glm3", requester="marina", why="w", vendor="opencode")
        pid3 = said.split()[1]
        rid3 = hr.load_proposal(pid3)["request"]
        con.execute("UPDATE requests SET state = 'done', verdict = 'approve' WHERE id = ?", (rid3,))
        subject, _why = hr.answered(hr.load_proposal(pid3), db.get_request(con, rid3))
        decisions.record("hr", pid3, rid3, "approved", subject)
        edited = hr.load_proposal(pid3)
        edited["repo"] = "/somewhere/else"
        hr.save(edited)
        ok, said = hr.apply(pid3, con)
        check("hr bind: a draft edited after the answer is refused, not applied",
              not ok and "changed after the founder answered" in said, said)
        edited["repo"] = None
        edited.pop("repo")
        hr.save(edited)
        ok, said = hr.apply(pid3, con)
        check("hr bind: put back as approved, it gets past the answer check to its own",
              "changed after" not in said, said)
    finally:
        con.close()


def test_an_answer_binds_the_escalation_the_founder_read(home: Path) -> None:
    """T182 (auditor #1043): through the founder's own resolve, a draft edited
    before the answer is not approved unseen, a reject settles whatever the
    draft says, and an unlink's live caveat is not part of the comparison."""
    use_home(home)
    (home / "team.yaml").write_text("""
agents:
  marina: {}
  keeper: {}
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
  - { from: "marina", to: "keeper", relation: peer, response: async }
""")
    for n in ("marina", "keeper"):
        agents.register(n)
    con = db.connect()
    try:
        def raise_add(name, **kw):
            ok, said = hr.propose("add", name, requester="marina", why="w", **kw)
            return said.split()[1]

        pid = raise_add("x1", repo="/repoA")
        prop = hr.load_proposal(pid)
        prop["repo"] = "/elsewhere"
        hr.save(prop)
        ok, said = askmod.resolve(prop["request"], "approve")
        check("bind: a field edited before the answer is not approved unseen",
              "not applied" in said and "raise it again" in said
              and decisions.get("hr", pid) is None and "x1" not in paths.list_agents(), said)

        pid = raise_add("x2")
        prop = hr.load_proposal(pid)
        prop["agent"] = "x2renamed"
        hr.save(prop)
        ok, said = askmod.resolve(prop["request"], "approve")
        check("bind: nor the agent it is about",
              "not applied" in said and "x2renamed" not in paths.list_agents(), said)

        pid = raise_add("x3", repo="/repoA")
        prop = hr.load_proposal(pid)
        prop["repo"] = "/elsewhere"
        hr.save(prop)
        ok, said = askmod.resolve(prop["request"], "reject - no")
        check("bind: a reject settles it whatever the draft says",
              hr.load_proposal(pid)["state"] == "rejected"
              and decisions.get("hr", pid)["call"] == "rejected", said)

        ok, said = hr.propose("unlink", "marina", requester="marina", to="keeper", why="w")
        upid = said.split()[1]
        uprop = hr.load_proposal(upid)
        row = db.get_request(con, uprop["request"])
        live = row["body"].replace("stay queued.", "stay queued. (a caveat that reads live)", 1)
        con.execute("UPDATE requests SET body = ? WHERE id = ?", (live, uprop["request"]))
        subject, why = hr.answered(uprop, db.get_request(con, uprop["request"]))
        check("bind: an unlink's live caveat is left out of the comparison",
              subject is not None and why is None, str(why))
    finally:
        con.close()


def test_register_writes_vendor_onto_an_agent_that_already_exists(home: Path) -> None:
    """auditor #1971's surviving mutation: register's existing-agent branch
    (`if vendor and vendor != DEFAULT_VENDOR: cfg["vendor"] = vendor`) had no
    test of its own. This is the orcapipe.glm case: the agent's folder is
    made first, by hand or by an earlier register, and the vendor arrives on
    a later call - hr's own re-register among them."""
    _daemon_home(home, 20)
    accounts.register("alpha")
    agents.register("late")
    cfg = yamlcompat.load(agents.agent_file("late").read_text())
    check("register existing: no vendor key yet", "vendor" not in cfg, str(cfg))

    agents.register("late", vendor="opencode")
    cfg = yamlcompat.load(agents.agent_file("late").read_text())
    check("register existing: a later call writes the vendor onto the "
          "already-registered agent", cfg.get("vendor") == "opencode", str(cfg))
    check("register existing: agent_vendor reads it as opencode",
          vendors.agent_vendor("late") == "opencode")


def test_a_dismissed_escalation_is_refused_by_its_state_not_as_a_quote(home: Path) -> None:
    """Task #70: a proposal whose escalation somehow closed as a dismissal is
    named as dismissed when apply refuses it, not as an empty answer."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("hr")
    hand_edit_team("""
agents:
  hr: {}
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
    con = db.connect()
    try:
        ok, said = hr.propose("add", "zed", requester="hr", why="hands")
        pid, rid = said.split()[1], int(said.split("#")[1].split(".")[0])
        con.execute("UPDATE requests SET state = 'done', dismissed_by = 'ghost', "
                    "dismissed_reason = 'not now' WHERE id = ?", (rid,))
        ok, msg = hr.apply(pid)
        check("dismissed escalation: apply names the state",
              not ok and "dismissed by ghost - not now" in msg, msg)
    finally:
        con.close()


def test_the_founders_approve_applies_the_proposal_and_tells_the_proposer(home: Path) -> None:
    """T156: an approve on a roster proposal's escalation carries it out right
    then, and the proposer's answer says how it went. A failed apply is put in
    front of the founder as a report and leaves the proposal open; a reject
    changes nothing but the record."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        accounts.register("beta")
        agents.register("hr")
        hand_edit_team("""
agents:
  hr: {}
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        ok, said = hr.propose("add", "dohyun", requester="hr", why="more hands")
        pid = said.split()[1]
        rid = int(said.split("#")[1].split(".")[0])
        ok, said = askmod.resolve(rid, "approve")
        check("approve: the agent exists with no `ocp hr apply`",
              ok and "dohyun" in paths.list_agents(), said)
        check("approve: the file record says applied, with the verdict",
              hr.load_proposal(pid)["state"] == "applied"
              and hr.load_proposal(pid)["verdict"] == "approve")
        check("approve: the proposer's answer carries the outcome",
              f"proposal {pid} applied" in db.get_request(con, rid)["verdict"]
              and db.get_request(con, rid)["verdict"].startswith("approve"),
              db.get_request(con, rid)["verdict"])

        # The apply fails: the table no longer lists the agent to be retired.
        ok, said = hr.propose("retire", "dohyun", requester="hr", why="gone")
        pid2 = said.split()[1]
        hand_edit_team("""
agents:
  hr: {}
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        rid2 = int(said.split("#")[1].split(".")[0])
        ok, said = askmod.resolve(rid2, "approve")
        row = db.get_request(con, rid2)
        check("approve: a failed apply is still an answer, and says it failed",
              ok and "could not be applied" in row["verdict"], row["verdict"])
        check("approve: and the proposal stays open to retry or withdraw",
              hr.load_proposal(pid2)["state"] == "proposed")
        check("approve: and the founder gets a report of it",
              con.execute("SELECT COUNT(*) c FROM requests WHERE fyi = 1 AND body LIKE ?",
                          (f"%{pid2}%could not be applied%",)).fetchone()["c"] == 1)

        ok, said = hr.propose("add", "yerin", requester="hr", why="two")
        pid3 = said.split()[1]
        rid3 = int(said.split("#")[1].split(".")[0])
        askmod.resolve(rid3, "reject - no")
        check("reject: nothing is applied", "yerin" not in paths.list_agents()
              and db.get_request(con, rid3)["verdict"] == "reject - no")
        check("reject: and the proposal's file records it", hr.load_proposal(pid3)["state"] == "rejected")

        ok, said = hr.propose("add", "mira", requester="hr", why="three")
        pid4 = said.split()[1]
        rid4 = int(said.split("#")[1].split(".")[0])
        ok, said = askmod.resolve(rid4, "reject - not yet\nask again next week")
        check("reject: a two-line reject still answers the proposer, and the file records it",
              ok and hr.load_proposal(pid4)["state"] == "rejected"
              and "ask again" in db.get_request(con, rid4)["verdict"], said)
    finally:
        con.close()


def test_every_door_onto_an_open_proposal_also_needs_a_decision(home: Path) -> None:
    """#1893 SF1, #1908, #1919, #1938, #1943: `answer()` (the bridge's key)
    is not the only door onto a queued proposal escalation - `ocp resolve`,
    `--retires`, `ask.cancel`, `--replaces` and `ask.withdraw` each reach the
    same row, and only `answer()` had the guard. A verdict, a retirement, a
    cancel, a replace or a withdraw typed there that is not a decision used
    to close the row anyway, leaving the proposal stuck exactly as if the
    bridge had let it through."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        accounts.register("beta")
        agents.register("hr")
        hand_edit_team("""
agents:
  hr: {}
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        ok, said = hr.propose("add", "dohyun", requester="hr", why="more hands")
        pid = said.split()[1]
        rid = int(said.split("#")[1].split(".")[0])
    finally:
        con.close()
    rc, out = _cli(["resolve", str(rid), "sounds good"], as_agent=askmod.HUMAN)
    con = db.connect()
    try:
        check("resolve: an open proposal refuses a non-decision here too",
              rc == 1 and "approve" in out and db.get_request(con, rid)["state"] == "queued",
              out)
    finally:
        con.close()
    rc, out = _cli(["resolve", str(rid), "approve"], as_agent=askmod.HUMAN)
    check("resolve: approve still goes through", rc == 0, out)
    check("resolve: and carries it out at once, saying so", "dohyun" in paths.list_agents()
          and "applied" in out and hr.load_proposal(pid)["state"] == "applied", out)
    check("resolve: so a later apply has nothing left to do", not hr.apply(pid)[0])

    # #1908 should-fix: the same escalation can be closed a third way, through
    # --retires on an unrelated resolve - "replaced by #N" is not a decision
    # either.
    ok, said = hr.propose("add", "yerin", requester="hr", why="a second pair")
    pid2 = said.split()[1]
    rid2 = int(said.split("#")[1].split(".")[0])
    ok, _, rid3 = askmod.ask(askmod.HUMAN, "unrelated question", requester="hr")
    check("retires: the unrelated ask queued", ok and rid3 is not None)
    rc, out = _cli(["resolve", str(rid3), "covered", "--retires", str(rid2)],
                   as_agent=askmod.HUMAN)
    check("retires: an open proposal refuses to be retired away",
          rc == 1 and "approve" in out, out)
    con = db.connect()
    try:
        check("retires: and stays queued", db.get_request(con, rid2)["state"] == "queued")
    finally:
        con.close()

    # #1919: task #32's finding - ask.cancel is the same family, since
    # CANCEL_VERDICT is not a decision either.
    con = db.connect()
    try:
        said = actions.cancel(con, rid2)
    finally:
        con.close()
    check("cancel: an open proposal refuses to be cancelled away",
          "approve" in said, said)
    con = db.connect()
    try:
        check("cancel: and stays queued", db.get_request(con, rid2)["state"] == "queued")
    finally:
        con.close()

    # #1938 should-fix: a fifth door - the proposer's own `--replaces` passes
    # the ownership check (old["requester"] == requester) and would close the
    # escalation with "replaced by #N", a row the founder cannot tell apart
    # from the original and that approving does not enact.
    ok, said, rid4 = askmod.ask(askmod.HUMAN, "corrected: add yerin on gamma",
                                requester="hr", replaces=rid2)
    check("replaces: an open proposal refuses to be replaced away",
          not ok and "withdraw" in said, said)
    check("replaces: no new row was queued for it", rid4 is None, rid4)
    con = db.connect()
    try:
        check("replaces: and stays queued", db.get_request(con, rid2)["state"] == "queued")
    finally:
        con.close()

    # #1943: a sixth door - the code review that found the chokepoint's
    # should-fixes also found this one. The proposer owns its own escalation
    # (it is the requester), so `ocp withdraw` - not `ocp hr withdraw` - is
    # ownership-eligible too, and would close it as "withdrawn: ..." if the
    # chokepoint let it.
    ok, said = askmod.withdraw(rid2, requester="hr")
    check("withdraw: an open proposal points at `hr withdraw` instead",
          not ok and "hr withdraw" in said, said)
    con = db.connect()
    try:
        check("withdraw: and stays queued", db.get_request(con, rid2)["state"] == "queued")
    finally:
        con.close()

    # `hr withdraw` is the escape valve, and still works on the same row.
    ok, said = hr.withdraw(pid2, "hr")
    check("hr withdraw: the one real way out still works", ok, said)
    con = db.connect()
    try:
        check("hr withdraw: and the row is finally closed",
              db.get_request(con, rid2)["state"] != "queued")
    finally:
        con.close()


def test_the_chokepoint_alone_refuses_every_door_onto_an_open_proposal(home: Path) -> None:
    """#1943: the test above proves each door's own, friendlier check works.
    This one proves the check those doors share does too - by calling the
    three ocp/db/ functions that can move a request row out of 'queued'
    directly, the way each door's own write eventually does, with none of
    the doors' own checks anywhere in the call path to catch it first. If
    this passed only because a door's check ran, deleting that check would
    not fail it; deleting `_would_strand_a_proposal` instead does.

    `close_if_queued` is `answer()`, `ocp resolve` and `ask.cancel`'s write.
    `retire_queued` is `--retires`'s, and `--replaces`'s when the row being
    replaced targets the founder - true of every proposal escalation, since
    only the founder decides one. `retire_if_undelivered` is `--replaces`'s
    write for a row targeting an agent instead; no proposal escalation ever
    takes that path, but the function is part of the same family and gets
    the same guard, so it is exercised here too."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        accounts.register("beta")
        agents.register("hr")
        hand_edit_team("""
agents:
  hr: {}
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        ok, said = hr.propose("add", "sooyoung", requester="hr", why="a third pair")
        rid = int(said.split("#")[1].split(".")[0])

        check("close_if_queued: refuses a non-decision verdict on an open proposal",
              db.close_if_queued(con, rid, "done", "looks good", announced=True) is False)
        check("close_if_queued: and stays queued", db.get_request(con, rid)["state"] == "queued")

        check("retire_queued: refuses to retire an open proposal away",
              db.retire_queued(con, rid, "replaced", "replaced by #9999") is False)
        check("retire_queued: and stays queued", db.get_request(con, rid)["state"] == "queued")

        check("retire_if_undelivered: refuses an open proposal too",
              db.retire_if_undelivered(con, rid, "replaced", "replaced") is False)
        check("retire_if_undelivered: and stays queued",
              db.get_request(con, rid)["state"] == "queued")

        check("close_if_queued: a real decision still goes through",
              db.close_if_queued(con, rid, "done", "approve") is True)
        check("close_if_queued: and the row is no longer queued",
              db.get_request(con, rid)["state"] == "done")

        # #2024: ocp/db/ once also held an unguarded state-setter, so the
        # docstring's "every function below" was false for it. That function
        # moved to this file as `_set_request_state`, test-only. Auditor
        # #2072 should-fix: pinning set_request_state's absence by name only
        # catches that name coming back, not a new unguarded door under a
        # different one - assert the property ocp/db/ must hold instead: every
        # function that writes requests.state calls _would_strand_a_proposal,
        # and the three that do are named here so this cannot pass by
        # finding none. Auditor #2094 should-fix: a raw substring match on
        # the function's source missed a writer whose SET clause orders
        # `state` after another column, and one written as two adjacent
        # string literals (a style ocp/db/ already uses) - Python folds
        # adjacent literals into one ast.Constant, so walk those instead of
        # the source text, and isolate the SET clause before looking for
        # `state =` in it, or a WHERE clause naming `state` (mark_delivered's
        # and _pre_schema_migrate's) reads as a writer too.
        import ast
        import re
        db_funcs = [n for f in sorted((ROOT / "ocp" / "db").glob("*.py"))
                    for n in ast.parse(f.read_text()).body]
        set_clause_re = re.compile(r"UPDATE\s+requests\s+SET\s+(.*?)(?:\bWHERE\b|$)",
                                   re.S | re.I)
        state_col_re = re.compile(r"(^|[\s,])state\s*=")
        guarded_writers, unguarded_writers = set(), set()
        for n in db_funcs:
            if not isinstance(n, ast.FunctionDef):
                continue
            writes_state = False
            for c in ast.walk(n):
                if not (isinstance(c, ast.Constant) and isinstance(c.value, str)):
                    continue
                m = set_clause_re.search(c.value)
                if m and state_col_re.search(m.group(1)):
                    writes_state = True
                    break
            if not writes_state:
                continue
            calls_guard = any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                and c.func.id == "_would_strand_a_proposal"
                for c in ast.walk(n))
            (guarded_writers if calls_guard else unguarded_writers).add(n.name)
        check("chokepoint: every db function writing requests.state "
              "calls _would_strand_a_proposal",
              not unguarded_writers and guarded_writers ==
              {"retire_queued", "close_if_queued", "retire_if_undelivered"},
              f"guarded={guarded_writers} unguarded={unguarded_writers}")
    finally:
        con.close()


def test_agents_that_wall_together_respect_the_cap_and_a_start_is_not_its_own_rival(home: Path) -> None:
    """Four agents walled at once on one account, cap 2: two move and two stay,
    because a moved agent counts on its new account until its next Stop. And an
    agent that is running again is not "something else" on its own start."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=100, pct_7d=10, reset_5h=now + 3 * 3600,
                 reset_7d=now + 86400)
    write_status(home, "beta", pct_5h=5, pct_7d=10, reset_5h=now + 3600, reset_7d=now + 86400)
    con = db.connect()
    try:
        names = ("a1", "a2", "a3", "a4")
        _running_on(con, "alpha", *names)
        for n in names:
            db.add_request(con, "marina", n, "carry on", None, "peer", "async", None, 0, None)
        moved = []
        for n in names:
            dest, _ = dmn_handoff._move_plan(con, n, "usage")
            if dest:
                db.set_account(con, n, dest)
                db.set_agent_state(con, n, dmn_states.QUEUED)
                db.add_event(con, n, "moved_account", {"from": "alpha", "to": dest})
                moved.append(n)
        check("cap: of four walled together, two move", len(moved) == 2, str(moved))

        db.add_event(con, moved[0], "Stop", {})
        check("cap: a moved agent stops counting once its turn has ended",
              loadmod.active_agents(con).get("beta", 0) == 1)

        write_status(home, "alpha", pct_5h=20, pct_7d=10, reset_5h=now + 3600,
                     reset_7d=now + 86400)
        db.set_agent_state(con, "a3", dmn_states.IDLE)
        db.set_agent_state(con, "a4", dmn_states.IDLE)
        db.set_agent_state(con, moved[1], dmn_states.IDLE)
        db.set_account(con, "a1", "alpha")
        db.set_agent_state(con, "a1", dmn_states.QUEUED)
        measure.begin(con, "a1", "alpha")
        db.add_event(con, "a1", "Stop", {})
        db.set_agent_state(con, "a1", dmn_states.RUNNING)
        write_status(home, "alpha", pct_5h=33, pct_7d=10, reset_5h=now + 3600,
                     reset_7d=now + 86400)
        measure.settle(con, "a1")
        n = con.execute("SELECT COUNT(*) FROM events WHERE kind = 'cold_start'").fetchone()[0]
        check("measure: the measured agent running again does not void its own sample",
              n == 1, str(n))
    finally:
        con.close()


def test_login_points_at_the_account_and_touches_no_credential(home: Path) -> None:
    """Logging in is the one thing ocp may not do for a human: a refresh token
    rotates on single use, so a second copy of one credential invalidates the
    first. All this decides is which config directory claude is pointed at."""
    use_home(home)
    accounts.register("alpha")

    action, msg = accounts.login_plan("beta")
    check("login: an account nobody registered says how to register it",
          action == "unknown" and "ocp account add beta" in msg, msg)

    action, msg = accounts.login_plan("alpha")
    check("login: one with no credentials gets the command that makes them",
          action == "login" and "claude /login" in msg, msg)
    check("login: pointed at that account's own config directory",
          str(vendors.config_dir("alpha")) in msg, msg)

    (vendors.config_dir("alpha") / ".credentials.json").write_text("{}")
    action, msg = accounts.login_plan("alpha")
    check("login: an account already logged in is left alone", action == "already", msg)
    action, _ = accounts.login_plan("alpha", force=True)
    check("login: unless the human insists", action == "login")

    # Which directory claude writes its credential into is the whole of what
    # this decides; ocp never reads or copies what lands there.
    env = accounts.env_for("alpha")
    check("login: the session is pointed at that account's config directory",
          env["CLAUDE_CONFIG_DIR"] == str(vendors.config_dir("alpha"))
          and env["OCP_ACCOUNT"] == "alpha", env["CLAUDE_CONFIG_DIR"])


def test_an_account_is_added_and_removed_from_ocp(home: Path) -> None:
    """Adding and removing an account are ocp commands, and removing one
    cannot strand an agent, orphan a credential, or lose what was spent."""
    use_home(home)
    hand_edit_team("agents:\n  backend: {}\n")
    for a in ("alpha", "beta", "gamma"):
        accounts.register(a)
    agents.register("backend")

    def run_cli(*argv):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = climod.main(list(argv))
        return rc, buf.getvalue() + err.getvalue()

    rc, out = run_cli("account", "add", "delta", "--no-login")
    check("account add: registers the account", rc == 0 and "delta" in paths.list_accounts(), out)
    rc, out = run_cli("account", "add", "delta", "--no-login")
    check("account add: refuses a name that exists", rc == 1, out)

    # An agent whose session actually went there.
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", "RUNNING")
        con.execute("UPDATE agent_state SET account = 'beta' WHERE agent = 'backend'")
        con.commit()
    finally:
        con.close()
    rc, out = run_cli("account", "remove", "beta", "--yes")
    check("account remove: refused while an agent's session is on it",
          rc == 1 and "backend" in out and "beta" in paths.list_accounts(), out)
    check("account remove: and the refusal names the action that frees it",
          "close" in out and "bridge" in out, out)
    # A closed agent has no session to strand: it stops holding the account, and
    # once the account is removed its dead resume id goes with it.
    con = db.connect()
    try:
        db.set_closed(con, "backend", "sess-old")
        con.commit()
    finally:
        con.close()
    rc, out = run_cli("account", "remove", "beta", "--yes")
    con = db.connect()
    try:
        row = db.get_agent_state(con, "backend")
        check("account remove: an account whose only user is closed is removed",
              rc == 0 and "beta" not in paths.list_accounts(), out)
        check("account remove: and the closed agent's record of it is cleared",
              row["account"] is None and row["resume_session"] is None, dict(row))
    finally:
        con.close()

    # A logout that does not take leaves the account where it is: moving the
    # directory would change the name its credential is filed under.
    orig_argv, orig_in = accounts.logout_argv, accounts.logged_in
    try:
        accounts.logout_argv = lambda a: (["false"], dict(os.environ))
        accounts.logged_in = lambda a: True
        rc, out = run_cli("account", "remove", "gamma", "--yes")
    finally:
        accounts.logout_argv, accounts.logged_in = orig_argv, orig_in
    check("account remove: a failed logout keeps the account",
          rc == 1 and "gamma" in paths.list_accounts(), out)

    rc, out = run_cli("account", "remove", "gamma", "--yes")
    retired = list((home / "accounts" / ".retired").glob("gamma-*"))
    check("account remove: takes it off the roster", rc == 0 and
          "gamma" not in paths.list_accounts(), out)
    check("account remove: and keeps the directory rather than deleting it",
          len(retired) == 1 and (retired[0] / ".claude").is_dir(), str(retired))

    rc, out = run_cli("account", "remove", "gamma", "--yes")
    check("account remove: an unknown account is said to be unknown", rc == 1, out)


def test_account_add_login_preflight_names_the_actual_blocker(home: Path) -> None:
    """The login preflight refuses for two different reasons - the vendor's
    binary is not on PATH, or stdin is not a terminal. One message for both
    sends a reader without the binary hunting for a terminal, and a reader
    in a script off to install something they have."""
    use_home(home)

    def run_cli(*argv):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = climod.main(list(argv))
        return rc, buf.getvalue() + err.getvalue()

    drv = vendors.get("claude")
    real_stdin = sys.stdin

    class NoTty:
        def isatty(self) -> bool:
            return False

    try:
        drv.binary = lambda: None
        rc, out = run_cli("account", "add", "delta")
        check("account add: a missing binary is named and said to be off PATH",
              rc == 1 and "claude was not found on PATH" in out
              and "terminal" not in out, out)

        drv.binary = lambda: "/x/bin/claude"
        sys.stdin = NoTty()
        rc, out = run_cli("account", "add", "delta")
        check("account add: no terminal says terminal, not PATH",
              rc == 1 and "needs a terminal you can type into" in out
              and "not found on PATH" not in out, out)
    finally:
        del drv.binary
        sys.stdin = real_stdin


def test_a_second_name_for_one_login_is_caught(home: Path) -> None:
    """Two accounts on one Anthropic login are one usage limit under two
    names, and their two copies of the credential invalidate each other."""
    use_home(home)
    for a in ("alpha", "beta", "gamma"):
        accounts.register(a)
    who = {"alpha": "a@x", "beta": "b@x", "gamma": "a@x"}
    orig_id, orig_in = accounts.identity, accounts.logged_in
    try:
        accounts.identity = lambda a: who[a]
        accounts.logged_in = lambda a: True
        check("same login: a duplicate is named", accounts.same_login_as("gamma") == ["alpha"],
              str(accounts.same_login_as("gamma")))
        check("same login: a distinct one is not", accounts.same_login_as("beta") == [],
              str(accounts.same_login_as("beta")))
        accounts.identity = lambda a: None
        check("same login: one claude cannot place is not called a duplicate",
              accounts.same_login_as("gamma") == [])
    finally:
        accounts.identity, accounts.logged_in = orig_id, orig_in


def test_team_and_agent_yaml_are_sealed_against_an_agents_hand(home: Path) -> None:
    """T188: an agent's sandbox can write team.yaml and agent.yaml, so a file
    that no longer matches what the founder's session last wrote is refused, and
    an agent's session cannot write either one through ocp."""
    _daemon_home(home, 20)
    agents.register("backend")
    hand_edit_team("agents:\n  backend: {}\n")
    check("sealed: the founder's own write reads back",
          "backend" in team.Team.load().agents and agents.read("backend")["name"] == "backend")

    tf, af = paths.team_file(), agents.agent_file("backend")
    tf.write_text("agents:\n  backend: {}\n  mallory: {}\n")
    af.write_text(af.read_text() + "repo: /elsewhere\n")
    for label, fn in (("team.yaml", team.Team.load), ("agent.yaml", lambda: agents.read("backend"))):
        try:
            fn()
            check(f"sealed: a hand-edited {label} is refused", False, "read instead of raising")
        except yamlcompat.ConfigError as e:
            check(f"sealed: a hand-edited {label} is refused", "founder" in str(e), str(e))
    row = _doctor_row(doctor.run(), "team.yaml was changed")
    check("sealed: doctor names the tampered team.yaml", row is not None and row[0] == doctor.FAIL,
          str(row))

    rc, out = cliteam_seal()
    check("sealed: the founder's `team seal` accepts both again",
          rc == 0 and "sealed 2" in out and "backend" in team.Team.load().agents
          and agents.read("backend")["repo"] == "/elsewhere", out)

    saved = os.environ.get("OCP_AGENT")
    os.environ["OCP_AGENT"] = "backend"
    try:
        before = (tf.read_text(), af.read_text())
        for label, fn in (("register", lambda: agents.register("backend", model="opus")),
                          ("register a new agent", lambda: agents.register("newbie")),
                          ("hr edit", lambda: hr._in_team_yaml("backend", "x", add=True))):
            try:
                fn()
                check(f"sealed: an agent's session cannot {label}", False, "it went through")
            except PermissionError as e:
                check(f"sealed: an agent's session cannot {label}", True, str(e))
        check("sealed: an agent's session cannot `team seal`", cliteam_seal()[0] == 1)
        check("sealed: and nothing was written", before == (tf.read_text(), af.read_text())
              and "newbie" not in paths.list_agents(), str(paths.list_agents()))
    finally:
        if saved is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = saved


def test_a_planted_or_unsealed_yaml_is_refused_and_doctor_seals_nothing(home: Path) -> None:
    """T188 audit: seals begin once. After that a folder planted for a new or a
    retired name is refused on the agent's and the founder's reads alike, and
    `ocp doctor` names it without sealing it; a db migration that rewrites
    team.yaml keeps its seal."""
    from ocp.db import schema
    _daemon_home(home, 20)
    agents.register("backend")
    hand_edit_team("agents:\n  backend: {}\n")
    agents.read("backend")

    def plant(name: str) -> Path:
        d = paths.agent_dir(name)
        d.mkdir(parents=True)
        f = d / "agent.yaml"
        f.write_text(f"name: {name}\nmodel: opus\nrepo: /elsewhere\n")
        return f

    plant("ghost")
    agents.register("gone")
    sealed.forget(sealed.agent_key("gone"))
    for name in ("ghost", "gone"):
        for who in (None, "backend"):
            saved = os.environ.get("OCP_AGENT")
            if who:
                os.environ["OCP_AGENT"] = who
            try:
                agents.read(name)
                check(f"unsealed: {name} is refused for {who or 'the founder'}", False, "read")
            except yamlcompat.ConfigError as e:
                check(f"unsealed: {name} is refused for {who or 'the founder'}",
                      "never sealed" in str(e), str(e))
            finally:
                if saved is None:
                    os.environ.pop("OCP_AGENT", None)
                else:
                    os.environ["OCP_AGENT"] = saved
    rep = doctor.run()
    for name in ("ghost", "gone"):
        row = _doctor_row(rep, f"agent {name}: agent.yaml was never sealed")
        check(f"unsealed: doctor names {name}", row is not None and row[0] == doctor.FAIL, str(row))
    check("unsealed: and doctor sealed nothing",
          all(sealed.state(sealed.agent_key(n), agents.agent_file(n).read_text()) == "unsealed"
              for n in ("ghost", "gone")))
    af = agents.agent_file("backend")
    af.write_text(af.read_text() + "repo: /x\n")
    row = _doctor_row(doctor.run(), "agent backend: agent.yaml was changed")
    check("changed: doctor names a tampered agent.yaml as plainly as team.yaml",
          row is not None and row[0] == doctor.FAIL, str(row))
    reseal(af)

    hand_edit_team("agents:\n  backend: {}\n  orchestrator: {}\n")
    con = db.connect()
    try:
        con.execute("DELETE FROM cursors WHERE name = 'migrated:human_name'")
        schema._rename_orchestrator(con)
    finally:
        con.close()
    check("migration: the human-name rewrite of team.yaml keeps it trusted",
          "orchestrator" not in paths.team_file().read_text() and "backend" in team.Team.load().agents)


def test_seals_begin_once_and_only_from_a_founder_read(home: Path) -> None:
    """T188 audit: on a home from before seals (files by hand, no marker) an
    agent's read accepts them and begins nothing, `ocp doctor` seals nothing,
    the first founder-side read seals every file and sets the marker, and a
    folder planted after that is refused."""
    from ocp.infra import protected
    _daemon_home(home, 20)
    tf = paths.team_file()
    tf.write_text("agents:\n  old: {}\n")
    d = paths.agent_dir("old")
    d.mkdir(parents=True)
    (d / "agent.yaml").write_text("name: old\nmodel: opus\n")
    marker = lambda: protected.get(sealed.NS, sealed.MIGRATED)  # noqa: E731
    key = sealed.agent_key("old")
    for name in (sealed.MIGRATED, sealed.TEAM, key):
        protected.delete(sealed.NS, name)

    saved = os.environ.get("OCP_AGENT")
    os.environ["OCP_AGENT"] = "old"
    try:
        check("legacy: an agent's read accepts a file no seal covers",
              agents.read("old")["name"] == "old" and "old" in team.Team.load().agents)
    finally:
        if saved is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = saved
    check("legacy: and begins nothing", marker() is None and protected.get(sealed.NS, key) is None)

    doctor.run()
    check("legacy: doctor sets no marker and seals nothing",
          marker() is None and protected.get(sealed.NS, key) is None
          and protected.get(sealed.NS, sealed.TEAM) is None)

    agents.read("old")
    check("legacy: the first founder-side read seals every file and sets the marker",
          marker() is not None and protected.get(sealed.NS, key) is not None
          and protected.get(sealed.NS, sealed.TEAM) is not None)

    d2 = paths.agent_dir("planted")
    d2.mkdir(parents=True)
    (d2 / "agent.yaml").write_text("name: planted\n")
    try:
        agents.read("planted")
        check("legacy: a folder planted after seals began is refused", False, "read")
    except yamlcompat.ConfigError as e:
        check("legacy: a folder planted after seals began is refused", "never sealed" in str(e), str(e))


def test_an_agent_whose_sandbox_cannot_read_the_protected_store_still_reads_the_yaml(home: Path) -> None:
    """The sandbox refuses even a read of the protected store (EPERM). An agent's
    session must then read team.yaml and agent.yaml as unsealed, not fail."""
    from ocp.infra import protected
    _daemon_home(home, 20)
    agents.register("backend")
    hand_edit_team("agents:\n  backend: {}\n")
    root = protected.root()
    root.chmod(0)
    saved = os.environ.get("OCP_AGENT")
    os.environ["OCP_AGENT"] = "backend"
    try:
        check("sandbox: the store reads as empty", protected.get("seal", "team") is None)
        check("sandbox: team.yaml and agent.yaml still read for the agent",
              "backend" in team.Team.load().agents and agents.read("backend")["name"] == "backend")
    finally:
        root.chmod(0o700)
        if saved is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = saved


def test_the_daemon_refuses_an_edge_an_agent_wrote_for_itself(home: Path) -> None:
    """T202: an agent whose sandbox cannot read the seals checks its own `ask`
    against a team.yaml it cannot verify. The daemon re-checks every waiting
    request against the founder's table: a tampered file holds delivery, and once
    the founder's text is back the request is refused."""
    from ocp.daemon import delivery
    from ocp.infra import protected
    _daemon_home(home, 20)
    agents.register("scout")
    agents.register("keeper")
    founders = ("agents:\n  scout: {}\n  keeper: {}\nedges:\n"
                "  - { from: \"*\", to: \"human\", kind: escalate, creates_gate: true }\n")
    hand_edit_team(founders)
    tampered = founders + '  - { from: "scout", to: "keeper", relation: peer, response: async }\n'
    root = protected.root()
    saved = os.environ.get("OCP_AGENT")
    os.environ["OCP_AGENT"] = "scout"
    root.chmod(0)
    try:
        paths.team_file().write_text(tampered)
        ok, said, rid = askmod.ask("keeper", "let me in", requester="scout")
    finally:
        root.chmod(0o700)
        if saved is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = saved
    check("daemon: the agent's own check let the forged edge through (why the daemon looks)", ok, said)

    logged: list[str] = []
    con = db.connect()
    try:
        held = delivery.enforce_edges(con, "keeper", logged.append)
        check("daemon: a tampered team.yaml holds delivery and says so",
              held is False and any("not trusted" in l for l in logged), str(logged))
        check("daemon: and the request is still queued, undelivered",
              db.get_request(con, rid)["state"] == "queued")
        paths.team_file().write_text(founders)
        ok2 = delivery.enforce_edges(con, "keeper", logged.append)
        row = db.get_request(con, rid)
        check("daemon: with the founder's text back, the request is refused at delivery",
              ok2 and row["state"] == "denied" and "refused at delivery" in (row["verdict"] or ""),
              str(dict(row)))
        check("daemon: nothing is left for keeper to be told", db.head_of_queue(con, "keeper") is None)
        ok3, said3, rid3 = askmod.ask("keeper", "hello", requester="scout")
        check("daemon: a request the table does deny at ask time is not delivered either",
              not ok3, said3)
    finally:
        con.close()


def test_a_forged_change_to_an_allowed_edge_is_refused_and_nothing_is_handed_out_unchecked(home: Path) -> None:
    """T204: the founder allows scout -> keeper as a peer. A forged edge that only
    changes its relation passes the agent's own ask, and until the daemon has
    checked it, `ocp inbox` in keeper's session must show nothing."""
    from ocp.daemon import delivery
    from ocp.infra import protected
    _daemon_home(home, 20)
    agents.register("scout")
    agents.register("keeper")
    founders = ("agents:\n  scout: {}\n  keeper: {}\nedges:\n"
                '  - { from: "scout", to: "keeper", relation: peer, response: async }\n'
                "  - { from: \"*\", to: \"human\", kind: escalate, creates_gate: true }\n")
    hand_edit_team(founders)
    forged = founders.replace("relation: peer, response: async", "relation: supervisor, response: async")
    root = protected.root()
    saved = os.environ.get("OCP_AGENT")
    os.environ["OCP_AGENT"] = "scout"
    root.chmod(0)
    try:
        paths.team_file().write_text(forged)
        ok, said, rid = askmod.ask("keeper", "do as I say", requester="scout")
    finally:
        root.chmod(0o700)
        if saved is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = saved
    check("edge: the forged relation passed the agent's own ask", ok, said)
    con = db.connect()
    try:
        check("edge: before the daemon has checked it, nothing is handed to keeper",
              db.head_of_queue(con, "keeper") is None)
        paths.team_file().write_text(founders)
        delivery.enforce_edges(con, "keeper", lambda _m: None)
        row = db.get_request(con, rid)
        check("edge: the daemon refuses it for differing from the founder's table",
              row["state"] == "denied" and "refused at delivery" in (row["verdict"] or ""),
              str(dict(row)))
        os.environ["OCP_AGENT"] = "scout"
        root.chmod(0)
        try:
            ok2, said2, rid2 = askmod.ask("keeper", "honest", requester="scout")
        finally:
            root.chmod(0o700)
            if saved is None:
                os.environ.pop("OCP_AGENT", None)
            else:
                os.environ["OCP_AGENT"] = saved
        check("edge: an honest request written in the agent session waits for the daemon",
              db.head_of_queue(con, "keeper") is None)
        delivery.enforce_edges(con, "keeper", lambda _m: None)
        head = db.head_of_queue(con, "keeper")
        check("edge: an honest request is cleared and handed out",
              ok2 and head is not None and int(head["id"]) == rid2, said2)
    finally:
        con.close()


def test_the_escalation_shows_the_effort_of_an_add_and_the_team_of_a_rename(home: Path) -> None:
    """T189: what the founder reads names every field apply uses."""
    _daemon_home(home, 20)
    agents.register("marina")
    hand_edit_team("teams:\n  eng: [marina]\nagents:\n  marina: {}\nedges:\n"
                   "  - { from: \"*\", to: \"human\", kind: escalate, creates_gate: true }\n")
    ok, said = hr.propose("add", "dohyun", requester="marina", why="hands", effort="high",
                          team_name="eng")
    check("escalation: the add was raised", ok, said)
    pid = said.split()[1]
    text = hr._escalation(hr.load_proposal(pid))
    check("escalation: an add shows its effort", ok and ", effort high" in text, text)
    ok, said = hr.propose("rename", "marina", requester="marina", why="new name", to="mari",
                          team_name="eng")
    check("escalation: the rename was raised", ok, said)
    text = hr._escalation(hr.load_proposal(said.split()[1]))
    check("escalation: a rename shows its team", ok and "would become mari in team eng" in text, text)


def cliteam_seal() -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = climod.main(["team", "seal"])
    return rc, buf.getvalue()


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_yaml())
        run(lambda: test_the_daemon_refuses_an_edge_an_agent_wrote_for_itself(Path(td) / 'edgeenf'))
        run(lambda: test_a_forged_change_to_an_allowed_edge_is_refused_and_nothing_is_handed_out_unchecked(Path(td) / 'edgeforge'))
        run(lambda: test_an_agent_whose_sandbox_cannot_read_the_protected_store_still_reads_the_yaml(Path(td) / 'noread'))
        run(lambda: test_the_escalation_shows_the_effort_of_an_add_and_the_team_of_a_rename(Path(td) / 'esceffort'))
        run(lambda: test_seals_begin_once_and_only_from_a_founder_read(Path(td) / 'sealbegin'))
        run(lambda: test_a_planted_or_unsealed_yaml_is_refused_and_doctor_seals_nothing(Path(td) / 'plantedyaml'))
        run(lambda: test_team_and_agent_yaml_are_sealed_against_an_agents_hand(Path(td) / 'sealedyaml'))
        run(lambda: test_agents(home))
        run(lambda: test_team_routing())
        run(lambda: test_yaml_differential())
        run(lambda: test_the_bundled_parser_reads_what_pyyaml_reads_as_strings_and_refuses_the_rest())
        run(lambda: test_yaml_rejects_ambiguity())
        run(lambda: test_a_poisoned_proposal_field_is_refused_at_the_write_not_read_back_on_the_founders_screen(
            Path(td) / 'poisonprop'))
        run(lambda: test_agents_read_refuses_a_value_a_hand_edit_wrote_with_a_control_character(
            Path(td) / 'readguard'))
        run(lambda: test_a_name_that_yaml_would_not_read_as_text_is_refused_where_it_is_typed(Path(td) / 'nameguard'))
        run(lambda: test_one_unreadable_proposal_does_not_take_the_hr_listing_down(Path(td) / 'hrlist'))
        run(lambda: test_a_rename_names_the_agents_orca_worktree(Path(td) / 'renamelabel'))
        run(lambda: test_a_renamed_agents_worktree_follows_it_with_its_work_and_memory(Path(td) / 'rehome'))
        run(lambda: test_hr_rehome_cli_moves_the_folder(Path(td) / 'rehomecli'))
        run(lambda: test_agent_yaml_is_written_by_dump_so_what_was_typed_reads_back(Path(td) / 'agentyaml'))
        run(lambda: test_yaml_booleans())
        run(lambda: test_team_validation())
        run(lambda: test_team_deny_validation())
        run(lambda: test_keychain_service_is_keyed_by_the_user_home(Path(td) / 'keychain'))
        run(lambda: test_a_claude_agent_is_never_placed_on_another_vendors_account(Path(td) / 'vendorpick'))
        run(lambda: test_another_vendors_account_never_spends_a_live_refresh(
            Path(td) / 'vendorrefresh'))
        run(lambda: test_a_second_vendors_live_session_is_seen_by_the_sweep(
            Path(td) / 'vendorsweep'))
        run(lambda: test_an_accounts_verdict_is_asked_for_its_own_vendor(
            Path(td) / 'vendorverdict'))
        run(lambda: test_an_unmetered_account_is_pinned_never_picked_or_polled(Path(td) / 'unmetered'))
        run(lambda: test_an_account_records_its_vendor(Path(td) / 'vendorrecord'))
        run(lambda: test_shared_dir_is_named_by_the_vendors_own_config_dir_name(Path(td) / 'shareddir'))
        run(lambda: test_an_account_links_from_its_own_vendors_shared_dir(Path(td) / 'vendorshared'))
        run(lambda: test_register_force_keeps_what_a_real_skills_dir_held(Path(td) / 'forceadopt'))
        run(lambda: test_register_force_plans_every_item_before_changing_one(Path(td) / 'planall'))
        run(lambda: test_a_refused_register_leaves_no_account_and_names_a_type_clash(Path(td) / 'refusedreg'))
        run(lambda: test_agent_memory_budget_migration_and_team_layer(Path(td) / 'memory'))
        run(lambda: test_team_rules_reach_the_prompt_and_doctor_reports_memory(Path(td) / 'teamrules'))
        run(lambda: test_the_opencode_driver(Path(td) / 'opencode'))
        run(lambda: test_an_opencode_session_under_ocp(Path(td) / 'ocsession'))
        run(lambda: test_a_clear_looks_the_vendor_up_once_and_refuses_in_its_own_words(Path(td) / 'clearonce'))
        run(lambda: test_an_opencode_row_takes_its_state_word_from_orca(Path(td) / 'ocrow'))
        run(lambda: test_shared_account_context_isolation(Path(td) / 'shared'))
        run(lambda: test_agent_rewrite_uses_parser(Path(td) / 'rw'))
        run(lambda: test_hr_unlink_says_when_a_wildcard_still_allows_it_and_link_wants_team_yaml(Path(td) / 'hrunl'))
        run(lambda: test_an_approve_on_a_team_proposal_is_recorded_and_applied(Path(td) / 'teamanswer'))
        run(lambda: test_hr_team_is_one_proposal_and_a_team_has_default_links(Path(td) / 'teamhrlink'))
        run(lambda: test_hr_link_and_unlink_edit_one_edge_and_nothing_else(Path(td) / 'hrlink'))
        run(lambda: test_hr_add_writes_the_vendor_it_was_given(Path(td) / 'hrvendor'))
        run(lambda: test_an_answer_binds_the_escalation_the_founder_read(Path(td) / 'hrbind'))
        run(lambda: test_register_writes_vendor_onto_an_agent_that_already_exists(
            Path(td) / 'registervendor'))
        run(lambda: test_a_dismissed_escalation_is_refused_by_its_state_not_as_a_quote(
            Path(td) / 'dismissedhr'))
        run(lambda: test_the_founders_approve_applies_the_proposal_and_tells_the_proposer(
            Path(td) / 'hrapplyonapprove'))
        run(lambda: test_every_door_onto_an_open_proposal_also_needs_a_decision(
            Path(td) / 'hrresolveguard'))
        run(lambda: test_the_chokepoint_alone_refuses_every_door_onto_an_open_proposal(
            Path(td) / 'hrchokepoint'))
        run(lambda: test_agents_that_wall_together_respect_the_cap_and_a_start_is_not_its_own_rival(Path(td) / 'ar4b'))
        run(lambda: test_login_points_at_the_account_and_touches_no_credential(Path(td) / 'login'))
        run(lambda: test_an_account_is_added_and_removed_from_ocp(Path(td) / 'acctaddrm'))
        run(lambda: test_account_add_login_preflight_names_the_actual_blocker(Path(td) / 'acctaddmsg'))
        run(lambda: test_a_second_name_for_one_login_is_caught(Path(td) / 'acctsame'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
