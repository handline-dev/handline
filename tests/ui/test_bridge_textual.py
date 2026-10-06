"""Tests for handline/ui/bridge_textual.py (task #45) - the port's first screen.

Needs `textual`, which the interpreter running `tests/test_ocp.py` may not
have (it is only in $OCP_HOME/venv - see requirements.txt and
handline/infra/venv.py's own docstring on why the suite cannot assume it everywhere).
`tests/test_ocp.py` runs this file under the first python that can import
textual (its own, else the venv's) and prints a SKIPPED line when none can, so
a missing dependency is said out loud instead of the screen going unexercised.

Run: python3 tests/ui/test_bridge_textual.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# First, so OCP_TEST_ROOT is set before any ocp module reads a path.
import test_ocp as t  # noqa: E402 - its `_daemon_home`/`DESIGN_TEAM` fixture, not re-derived here
from textual import events  # noqa: E402
from handline.actions import dialogs as dialogs_mod  # noqa: E402
from handline import actions, db
from handline.team import accounts, agents, hr
from handline.ui import bridge_textual as bt, jobs as jobsmod, tui
from handline.infra import timefmt  # noqa: E402
from handline.daemon import states as dmn_states
from handline.ui.chat_page import ChatMessage as cp_ChatMessage, ChatPage  # noqa: E402
from handline.ui.clipboard_app import ClipboardApp  # noqa: E402
from handline.ui.components.confirm import Confirm, confirm_answer  # noqa: E402
from textual import events  # noqa: E402
from handline.ui.components.text_prompt import TextPrompt  # noqa: E402
from handline.ui.pages.account import AccountPage  # noqa: E402
from handline.ui.pages.home import HomePage  # noqa: E402
from textual.widgets import DataTable, Static  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))


def _said(screen) -> str:
    """The newest toast the app has shown."""
    notes = list(screen.app._notifications)
    return notes[-1].message if notes else ""


def _home(tag: str) -> Path:
    """`t._daemon_home`, minus the daemon-trigger fixture (a percentage, a
    Stop event) this screen has no use for: registers `backend` on `alpha`
    with a terminal, and a real team.yaml so `actions.chat_reply`/`ask` have
    somewhere to route through."""
    home = Path(tempfile.mkdtemp(prefix=f"ocp_bt_{tag}_", dir=t.TEST_ROOT))
    t._daemon_home(home, 20)
    return home


def _screen_text(screen) -> str:
    return "\n".join(str(w.render()) for w in screen.query(Static))


def cp_verbs():
    from handline.ui import chat_page as cp
    return cp.CHAT_VERBS


def test_key_guide_keeps_the_high_ranks_on_one_line_and_ends_with_a_question_mark() -> None:
    from handline.ui.components.key_guide import Key, fit
    keys = [Key("a", "alpha", 1), Key("b", "bravo", 3), Key("c", "charlie", 2), Key("d", "delta", 1)]
    wide = fit(keys, 80).plain
    check("key guide: with room, every key in the order given, then ?",
          wide == "a alpha · b bravo · c charlie · d delta · ? keys", wide)
    for width in (12, 24, 31, 40):
        line = fit(keys, width)
        check(f"key guide: {width} cells is one line that fits and ends with ?",
              line.cell_len <= width and line.plain.endswith("? keys"), line.plain)
    tight = fit(keys, 31).plain
    check("key guide: the lowest rank goes first", "bravo" not in tight and "alpha" in tight and "delta" in tight, tight)


def test_key_guide_never_keeps_a_lower_rank_while_a_higher_one_is_left_out() -> None:
    """T149: a wide rank-1 key that alone still does not fit used to just be skipped, leaving
    room a rank-3 key after it would then fill - so the guide showed the least important key
    while dropping a more important one. The rank-3 key must go first regardless."""
    from handline.ui.components.key_guide import Key, fit
    keys = [Key("a", "aa", 1), Key("b", "b" * 10, 1), Key("z", "z", 3)]
    line = fit(keys, 19).plain
    check("key guide: the rank-3 key is dropped before the rank-1 one, even though that one still does not fit",
          "z" not in line and "aa" in line and "b" * 10 not in line, line)


def _week_sample(con, account: str, ts: int, used: int, reset: int) -> None:
    import json as _json
    con.execute("INSERT INTO usage_samples(account, ts, exists_, windows) VALUES(?,?,1,?)",
                (account, ts, _json.dumps([
                    {"minutes": 300, "used_percent": 10, "resets_at": ts + 3600, "observed_at": ts},
                    {"minutes": 10080, "used_percent": used, "resets_at": reset, "observed_at": ts}])))


def test_the_weekly_outlook_averages_accounts_and_restores_at_reset() -> None:
    from handline.usage import outlook
    _home("outlook")
    con = db.connect()
    try:
        now = 1_800_000_000
        for h in range(24, -1, -1):
            _week_sample(con, "alpha", now - h * 3600, 50 - h, now + 6 * 3600)     # 1 point an hour
            _week_sample(con, "beta", now - h * 3600, 20, now + 3 * 86400)         # idle
        con.commit()
        real, accounts.login_state = accounts.login_state, lambda a: True     # a logged-out account is left out
        try:
            o = actions.weekly_outlook(con, now)
        finally:
            accounts.login_state = real
    finally:
        con.close()
    now_left = o.past[-1][1]
    check("outlook: two accounts are counted", o.accounts == 2, o.accounts)
    check("outlook: the headroom now is the mean of what is left (alpha 50 used, beta 20)",
          now_left == 65, now_left)
    at_reset = dict(o.future)[now + 6 * 3600] if (now + 6 * 3600) in dict(o.future) else None
    check("outlook: after alpha's week resets what it used comes back", at_reset is not None and at_reset > now_left - 5,
          (at_reset, now_left))
    check("outlook: the guess ends inside 24h", o.future[-1][0] <= now + 24 * 3600, o.future[-1][0])
    con = db.connect()
    try:
        used = outlook.week_consumed(db.usage_samples(con), now - 24 * 3600, now)
    finally:
        con.close()
    check("outlook: the points used are the rises (alpha 26 to 50 = 24, beta idle = 0)",
          used == {"alpha": 24.0, "beta": 0.0}, used)
    flat = outlook.weekly_outlook([], now)
    check("outlook: no samples, no points", not flat.past and not flat.future and flat.accounts == 0)


def test_an_account_that_ends_steps_the_forecast_down_by_its_share() -> None:
    from handline.usage import outlook
    _home("outlook-ends")
    con = db.connect()
    try:
        now = 1_800_000_000
        for h in range(24, -1, -1):
            _week_sample(con, "alpha", now - h * 3600, 50, now + 3 * 86400)
            _week_sample(con, "beta", now - h * 3600, 50, now + 3 * 86400)
        con.commit()
        rows = db.usage_samples(con)
    finally:
        con.close()
    end = now + 6 * 3600
    o = outlook.weekly_outlook(rows, now, 24, {"alpha": end})
    future = dict(o.future)
    before = [v for t, v in o.future if t < end]
    after = [v for t, v in o.future if t > end]
    check("outlook ends: before the end both accounts count, 50% left", before and all(v == 50 for v in before), str(before[:3]))
    check("outlook ends: after it the line is 25, the ended account's share gone, not the other's 50",
          after and all(v == 25 for v in after), str(after[:3]))
    check("outlook ends: looking back, nothing changes", o.past[-1][1] == 50)
    renewing = outlook.weekly_outlook(rows, now, 24, {})
    check("outlook ends: with no end it stays at 50", all(v == 50 for _, v in renewing.future))
    gone = outlook.weekly_outlook(rows, now, 24, {"alpha": now - 3600})
    check("outlook ends: one that ended already is not in the average at all", all(v == 50 for _, v in gone.future if v is not None))


def test_the_overlay_draws_what_the_agent_ate_in_front_of_the_headroom_and_shares_add_up() -> None:
    from handline.ui.components.outlook_view import chart
    from handline.usage.outlook import Outlook
    o = Outlook(past=[(0, 50.0), (1, 50.0)], future=[(2, 50.0)], accounts=1)
    plain = chart(o, 30, 8, "24h")
    over = chart(o, 30, 8, "24h", [20.0, 20.0])
    check("overlay: without it there is no magenta", not any("magenta" in str(sp.style) for sp in plain.spans))
    check("overlay: with it the agent's part is drawn in magenta in front of the headroom",
          any("magenta" in str(sp.style) for sp in over.spans))
    _home("agentseries")
    con = db.connect()
    try:
        now = 1_800_000_000
        con.execute("INSERT INTO agent_state(agent, state, updated, worktree) VALUES ('writer', 'IDLE', 0, '/w/writer')")
        con.execute("INSERT INTO token_calls VALUES('alpha','p','p1',?,'claude-sonnet-5','/w/writer',0,1000,0,0,200,0)",
                    (now - 3600,))
        con.execute("INSERT INTO token_calls VALUES('alpha','p','p2',?,'claude-sonnet-5','/w/other',0,1000,0,0,200,0)",
                    (now - 3600,))
        con.commit()
        uses = {u.agent: u for u in actions.agent_use(con, now, 24)}
    finally:
        con.close()
    from handline.usage import stats as ustats
    con = db.connect()
    try:
        est = {u.agent: u.est_pct for u in ustats.agent_use(con, now - 86400, now, None, {"alpha": 10.0})}
    finally:
        con.close()
    check("agent use: an agent's share of the week is its cost share times the account's points",
          abs(est["writer"] - 5.0) < 1e-6 and abs(est["(unattributed)"] - 5.0) < 1e-6, est)
    check("agent use: the table splits the same calls between the agents",
          set(uses) == {"writer", "(unattributed)"} and abs(uses["writer"].share - 0.5) < 1e-6, uses)


def test_the_agents_table_is_a_tree_with_the_group_total_on_top() -> None:
    from handline.usage.stats import AgentUse, group_uses
    uses = [AgentUse("orcapipe.ui", 10, 3.0, 0.3, 1.0, {"a": 0.3}), AgentUse("orcapipe", 5, 6.0, 0.6, 2.0, {"a": 0.6}),
            AgentUse("harbor", 1, 1.0, 0.1, 0.5, {"a": 0.1})]
    rows = group_uses(uses)
    got = [(r.key, r.label, r.depth) for r in rows]
    check("tree: the group with the most cost first, its total on top, then self and the children",
          got == [("grp:orcapipe", "orcapipe", 0), ("orcapipe.ui", "ui", 1), ("orcapipe", "self", 1),
                  ("harbor", "harbor", 0)], got)
    top = rows[0].use
    check("tree: the group row is the sum of its members", top.tokens == 15 and abs(top.cost - 9.0) < 1e-9
          and abs(top.est_pct - 3.0) < 1e-9 and abs(top.fracs["a"] - 0.9) < 1e-9, top)


def test_the_outlook_chart_draws_a_column_per_slot_with_the_guess_apart() -> None:
    from handline.ui.components.outlook_view import chart
    from handline.usage.outlook import Outlook
    o = Outlook(past=[(0, 100.0), (1, 50.0)], future=[(2, 25.0), (3, 0.0)], accounts=1)
    text = chart(o, 40, 8)
    check("chart: one line per row and an axis", len(text.plain.split("\n")) == 9, text.plain)
    styles = {str(sp.style) for sp in text.spans}
    check("chart: the recorded part and the guess have their own colours", {"cyan", "yellow"} <= styles, styles)


def test_only_y_n_or_escape_answers_a_confirm() -> None:
    """A wheel notch reaches a confirm as an arrow key and must not answer it."""
    for key, ch in (("up", None), ("down", None), ("left", None), ("right", None),
                    ("enter", "\r"), ("space", " "), ("x", "x"), ("pageup", None)):
        check(f"confirm: {key} leaves it open", confirm_answer(key, ch) is None)
    check("confirm: y confirms", confirm_answer("y", "y") is True)
    check("confirm: Y confirms", confirm_answer("Y", "Y") is True)
    check("confirm: n declines", confirm_answer("n", "n") is False)
    check("confirm: escape declines", confirm_answer("escape", "\x1b") is False)
    check("confirm: ctrl+c declines", confirm_answer("ctrl+c", "\x03") is False)


def test_the_menu_offers_what_the_text_could_become() -> None:
    from handline.ui import chat_page as cp
    tasks = [(84, "owns", "waiting", "keeper fix cache", "orcapipe", 300),
             (85, "owns", "doing", "clerk discard", "orcapipe", 200),
             (7, "opened", "done", "auditor pass", "orcapipe", 100)]
    reqs = [(2305, "human -> orcapipe", "first line"), (2289, "human -> orcapipe", "other")]

    def names(text):
        return [x.label[0] for x in cp.suggest(text, tasks, reqs)]
    check("menu: a slash lists every page command",
          names("/") == ["/chat", "/all", "/task", "/sort", "/settings", "/back", "/help", "/exit"], str(names("/")))
    check("menu: a slash narrows by the letters typed", names("/ta") == ["/task"], str(names("/ta")))
    check("menu: /task lists the tasks, newest change first", names("/task ") == ["T84", "T85", "T7"])
    check("menu: /task narrows by a word of the title", names("/task audit") == ["T7"])
    check("menu: /task narrows by the digits of the number", names("/task 8") == ["T84", "T85"])
    check("menu: /sort names the columns", names("/sort tit") == ["title"], str(names("/sort tit")))
    check("menu: a hash lists tasks then requests", names("see #") == ["T84", "T85", "T7", "#2305", "#2289"])
    check("menu: /task also takes the T form, and a task inserts as T<n> but a request as its bare number",
          names("/task t8") == ["T84", "T85"]
          and [x.arg for x in cp.suggest("see #", tasks, reqs)][:1] == ["T84"]
          and [x.arg for x in cp.suggest("see #", tasks, reqs)][3:4] == ["2305"])
    check("menu: a hash narrows by the digits after it", names("see #23") == ["#2305"])
    check("menu: a hash not after a space, or with letters, offers nothing",
          names("a#2") == [] and names("a #zz") == [])
    check("menu: plain text and unknown commands offer nothing", names("hello") == [] and names("/nope") == [])
    check("menu: text after a newline is never a command", names("hi\n/task") == [])
    pick = cp.suggest("/task 8", tasks, reqs)[0]
    check("menu: Tab on a task fills the command, Enter opens it",
          pick.tab == "/task 84" and pick.action == "open" and pick.arg == "84")
    ref = cp.suggest("see #", tasks, reqs)[0]
    check("menu: a task reference inserts T<n>", ref.action == "insert" and ref.arg == "T84")
    verbs = cp.suggest("/w", tasks, reqs, ("terminal", "wake"))
    check("menu: the agent actions on offer are listed after the page commands and narrow by letters",
          [(x.label[0], x.action) for x in verbs] == [("/wake", "verb")]
          and [x.label[0] for x in cp.suggest("/", tasks, reqs, ("move",))][-1] == "/move"
          and "/move" not in names("/"))


def test_the_task_pages_write_a_task_as_T_and_a_request_as_a_bare_number() -> None:
    from handline.ui import chat_page as cp
    d = {"id": 1, "title": "t", "state": "doing", "owner": "o", "opened_by": "o", "created_at": 1,
         "parent": (9, "up"), "done": None, "discarded": None, "merged_into": None, "asker": "o",
         "children": [(2, "kid", "waiting")],
         "timeline": [{"ts": 1, "who": "o", "kind": "memo", "text": "a memo\nsecond line", "memo": 1, "edited_at": 2},
                      {"ts": 2, "who": "a → b", "kind": "request", "text": "#3  queued  line", "memo": None, "edited_at": None,
                       "ref": "#3", "state": "queued", "summary": "line"}]}
    page = cp.task_page(d)
    check("page: the badge and the parent and the sub-tasks say T<n>",
          "T1" in page["badge"].plain and "T9" in page["meta"].plain
          and page["kids"][0].plain.startswith("T2"), str((page["badge"].plain, page["meta"].plain)))
    check("page: a request is a timeline entry, a bare #<n>", "↗ request  a → b  #3  queued  line" in page["timeline"][1].plain
          and "reqs" not in page)
    line = page["timeline"][0].plain
    check("page: a timeline entry is its first line, then the rest of its text, and when it was edited",
          "✎ memo  o  a memo" in line and line.split("\n")[1:] == ["    second line  · edited 1970-01-01 09:00"], line)


def test_the_typed_history_keeps_the_newest_and_never_repeats() -> None:
    from handline.ui import chat_page as cp
    from handline.infra import paths
    _home("hist")
    check("history: a repeat moves up instead of doubling", cp.remember(["a", "b", "c"], "b") == ["a", "c", "b"])
    check("history: blank is not remembered", cp.remember(["a"], "  \n") == ["a"])
    full = [str(i) for i in range(cp.HISTORY_MAX)]
    grown = cp.remember(full, "new")
    check("history: only the newest HISTORY_MAX are kept",
          len(grown) == cp.HISTORY_MAX and grown[-1] == "new" and grown[0] == "1")
    check("history: matches are newest first and ignore case",
          cp.history_matches("AB", ["xab", "no", "ab2"]) == ["ab2", "xab"])
    check("history: no file is an empty history", cp.load_history("backend") == [])
    cp.save_history("backend", ["one", "two é"])
    check("history: it survives a reload", cp.load_history("backend") == ["one", "two é"]
          and str(cp.history_file()).startswith(str(paths.home())))
    cp.save_history("reviewer", ["other"])
    check("history: each room keeps its own",
          cp.load_history("backend") == ["one", "two é"] and cp.load_history("reviewer") == ["other"]
          and cp.load_history("product") == [])
    cp.history_file().write_text('["old", "shared"]')
    check("history: the list from before rooms is dropped",
          cp.load_history("backend") == [] == cp.load_history("reviewer"))
    cp.save_history("backend", ["new"])
    check("history: and the next save starts a per-room file",
          cp.load_history("backend") == ["new"] and cp.load_history("reviewer") == [])
    cp.history_file().write_text("{not json")
    check("history: a corrupt file is an empty history, not an error", cp.load_history("backend") == [])
    cp.save_history("backend", ["again"])
    check("history: and the next save starts a good file", cp.load_history("backend") == ["again"])
    cp.history_file().write_text('{"rooms": {"backend": [1, "ok", null]}}')
    check("history: only strings are read back", cp.load_history("backend") == ["ok"])
    cp.history_file().write_text(json.dumps({"rooms": {"backend": [str(i) for i in range(150)]}}))
    check("history: a room reads at most HISTORY_MAX", cp.load_history("backend")[0] == "50"
          and len(cp.load_history("backend")) == cp.HISTORY_MAX)


def test_the_chat_page_reads_a_tasks_state_only_for_a_task_delivery() -> None:
    """T429: a kind=task row comes with its task's live state (the card draws it); a
    plain message linked to a task does not, so the page pays for no other task_state."""
    from handline.ui import chat_page as cp
    _home("chatstate")
    agents.register("reviewer")
    con = db.connect()
    try:
        ok, _, tid = actions.task_new(actions.HUMAN, "reviewer", "Fix it", "the work and its done")
        actions.chat_reply(con, "reviewer", "just a say")
        say = actions.chat_parent(con, "reviewer")
        db.link_request(con, tid, say)
        con.commit()
        by = {r["id"]: r for r in cp.requests_page(con, "reviewer", False)[0]}
        card = next(r for r in by.values() if r["kind"] == "task")
        live = actions.task_state(con, db.get_task(con, tid))
        check("chat page: a task delivery carries its task's live state",
              ok and card["task_state"] == live and live is not None, str(dict(card)))
        check("chat page: a say linked to a task carries no state", by[say]["task_state"] is None, str(dict(by[say])))
    finally:
        con.close()


def test_background_work_alone_is_not_working() -> None:
    """Founder #5308: an agent between turns with only background work running reads idle,
    with the background note beside it - never working."""
    from handline.daemon import delivery, states
    _home("bgidle")
    agents.register("reviewer")
    con = db.connect()
    was = delivery.has_background_work
    try:
        delivery.has_background_work = lambda c, a: True
        for st in (states.QUEUED, states.IDLE):
            db.set_agent_state(con, "reviewer", st)
            word, detail = tui.screen_state(con, db.get_agent_state(con, "reviewer"))
            check(f"background only ({st}): idle, not working, with the background note",
                  word == "IDLE" and detail.startswith("background"), str((word, detail)))
    finally:
        delivery.has_background_work = was
        con.close()


def test_the_chat_numbers_an_answer_by_its_own_row() -> None:
    """T421: an answer is a message of its own, so the chat gives the ask and the
    answer two numbers, draws each once, and a reply under the answer counts as
    the reply to the ask."""
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("chatanswer")
    agents.register("reviewer")
    con = db.connect()
    try:
        actions.chat_reply(con, "reviewer", "which one?")
        q = actions.chat_parent(con, "reviewer")
        askmod.resolve(q, "this one")
        rows, _ = cp.requests_page(con, "reviewer", False)
        check("chat: the answer row is not a request of its own in the page",
              [r["id"] for r in rows] == [q] and rows[0]["answer_id"] is not None, str([dict(r) for r in rows]))
        msgs = cp.messages_of(rows, "reviewer")
        check("chat: the ask and the answer carry two different numbers",
              [(m["part"], m["mid"]) for m in msgs] == [(0, q), (1, rows[0]["answer_id"])]
              and rows[0]["answer_id"] != q, str(msgs))
        askmod.ask("reviewer", "thanks", requester=actions.HUMAN, parent_id=rows[0]["answer_id"])
        after = cp.messages_of(cp.requests_page(con, "reviewer", False)[0], "reviewer")
        check("chat: a reply to the answer names the answer's number, in time order",
              [(m["part"], m["reply_to"]) for m in after] == [(0, None), (1, None), (0, rows[0]["answer_id"])]
              and cp.mid_of(after[1]) == rows[0]["answer_id"], str(after))
    finally:
        con.close()


def test_the_chat_is_paged_and_hides_what_never_reached_the_agent() -> None:
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("chat")
    agents.register("reviewer")
    con = db.connect()
    try:
        for body in ("one", "two", "three"):
            actions.chat_reply(con, "reviewer", body)
        rows, more = cp.requests_page(con, "reviewer", False, limit=2)
        check("chat: a page is the newest requests, oldest first, and says more remain",
              [r["body"] for r in rows] == ["two", "three"] and more)
        older, more = cp.requests_page(con, "reviewer", False, before=rows[0]["id"], limit=2)
        check("chat: the page before it holds the rest", [r["body"] for r in older] == ["one"] and not more)
        sig = cp.signature(con, "reviewer", False)
        second = rows[0]["id"]
        first = second - 1
        askmod.cancel(second)
        check("chat: a request taken back before delivery leaves the chat",
              [r["body"] for r in cp.requests_page(con, "reviewer", False)[0]] == ["one", "three"])
        check("chat: and the change is seen by the cheap reading", cp.signature(con, "reviewer", False) != sig)
        msgs = cp.messages_of(cp.requests_page(con, "reviewer", False)[0], "reviewer")
        check("chat: a queued message can be cancelled, not yet delivered",
              msgs[0]["queued"] and not msgs[0]["delivered"] and msgs[0]["side"] == "you", str(msgs[0]))
        con.execute("UPDATE requests SET delivered_at = ?, typed_at = ?, landed_at = ?, state = 'landed' WHERE id = ?",
                    (int(time.time()),) * 3 + (first,))
        con.commit()
        check("chat: once delivered it says so",
              cp.messages_of(cp.requests_page(con, "reviewer", False)[0], "reviewer")[0]["delivered"])
        ok, msg = askmod.cancel(first)
        check("chat: once it landed it cannot be cancelled - it is landed, and stays as it was",
              not ok and "already landed" in msg and db.get_request(con, first)["state"] == "landed", msg)
    finally:
        con.close()


def _page_rows(con, rid: int) -> list:
    from handline.ui import chat_page as cp
    return [r for r in cp.requests_page(con, "reviewer", False)[0] if r["id"] == rid]


def test_an_hr_card_is_drawn_only_in_the_chats_it_concerns() -> None:
    from handline.ui import chat_page as cp
    add = {"id": "a1", "kind": "add", "agent": "boss.scout", "requested_by": "boss", "team": "boss"}
    check("hr scope: the proposer, the agent added and the team's manager see an add",
          all(cp.hr_concerns(add) >= {n} for n in ("boss", "boss.scout")))
    check("hr scope: another agent does not", "legacy" not in cp.hr_concerns(add))
    from handline.ui.components import hr_card
    real = hr_card.pending
    hr_card.pending = lambda since=0: [{**add, "ts": 5, "state": "applied"}]
    try:
        check("hr scope: the card is drawn for boss and not for legacy",
              len(cp.hr_cards("boss", [])) == 1 and cp.hr_cards("legacy", []) == [])
    finally:
        hr_card.pending = real
    rename = {"kind": "rename", "agent": "old", "requested_by": "boss", "to": "new", "team": "mgr"}
    check("hr scope: a rename concerns the old name, the new one, the proposer and the manager",
          cp.hr_concerns(rename) == {"old", "new", "boss", "mgr"}, str(cp.hr_concerns(rename)))
    team = {"kind": "team", "agent": "mgr", "requested_by": "mgr", "members": "mgr.a,mgr.b"}
    check("hr scope: a new team concerns its manager and members", cp.hr_concerns(team) == {"mgr", "mgr.a", "mgr.b"})
    check("hr scope: a request with nothing optional is its proposer and its agent",
          cp.hr_concerns({"kind": "settings", "agent": "x", "requested_by": None}) == {"x"})


def test_a_message_number_finds_its_chat_and_its_page() -> None:
    from handline.ui import chat_page as cp
    _home("locate")
    for n in ("backend", "frontend", "reviewer"):
        agents.register(n)
    human = actions.HUMAN
    con = db.connect()

    def add(to: str, body: str, requester: str) -> int:
        cur = con.execute("INSERT INTO requests(ts, requester, target, body, state) VALUES(?,?,?,?,'queued')",
                          (int(time.time()), requester, to, body))
        return cur.lastrowid
    try:
        ids = {}
        ids["mine"] = add("backend", "to backend", human)
        ids["b_f"] = add("frontend", "backend asks frontend", "backend")
        ids["f_r"] = add("reviewer", "frontend asks reviewer", "frontend")
        ids["r_human"] = add(human, "reviewer tells you", "reviewer")
        ids["old"] = add("backend", "the oldest", human)
        for i in range(7):
            add("backend", f"filler {i}", human)
        con.commit()
        at = lambda mid, agent, everyone: cp.locate_message(con, mid, agent, everyone)
        check("locate: a message between the founder and this agent is here, on its own chat",
              at(ids["mine"], "backend", False) == cp.Located("here", ids["mine"], "backend", "mine"))
        check("locate: one between this agent and another is here but in the all-chat, which flips the tab",
              at(ids["b_f"], "backend", False) == cp.Located("here", ids["b_f"], "backend", "all"))
        check("locate: from the all-chat the founder's message stays there",
              at(ids["mine"], "backend", True).tab == "all" and at(ids["mine"], "backend", True).kind == "here")
        check("locate: a message of two other agents is in the all-chat of the one that sent it",
              at(ids["f_r"], "backend", False) == cp.Located("chat", ids["f_r"], "frontend", "all"))
        check("locate: one between the founder and another agent is that agent's own chat",
              at(ids["r_human"], "backend", False) == cp.Located("chat", ids["r_human"], "reviewer", "mine"))
        check("locate: no such number is alone", at(999999, "backend", False).kind == "alone")
        cur = con.execute("INSERT INTO requests(ts, requester, target, body, state, settles) VALUES(?,?,?,?,'queued',?)",
                          (int(time.time()), "frontend", "backend", "the answer", ids["b_f"]))
        answer = cur.lastrowid
        check("locate: the number of an answer is the request it settles, found where that request is",
              at(answer, "backend", False) == cp.Located("here", ids["b_f"], "backend", "all")
              and at(answer, "reviewer", False) == cp.Located("chat", ids["b_f"], "backend", "all"))
        orphan = con.execute("INSERT INTO requests(ts, requester, target, body, state, settles) VALUES(?,?,?,?,'queued',?)",
                             (int(time.time()), "frontend", "backend", "an answer to nothing", 999998)).lastrowid
        check("locate: an answer whose request is gone is alone", at(orphan, "backend", False).kind == "alone")
        check("locate: its text is there to show alone",
              cp.message_alone(con, ids["f_r"]) == (f"#{ids['f_r']}  frontend \u2192 reviewer", "frontend asks reviewer")
              and cp.message_alone(con, 999999) is None)
        con.execute("UPDATE requests SET state = 'withdrawn' WHERE id = ?", (ids["f_r"],))
        check("locate: one that never reached anyone, no chat draws, is alone",
              at(ids["f_r"], "backend", False).kind == "alone")
        rows, more = cp.page_back_to(con, "backend", False, None, limit=3)
        check("page: with no message asked for it is the newest page",
              len(rows) == 3 and more and ids["old"] not in {r["id"] for r in rows})
        rows, more = cp.page_back_to(con, "backend", False, ids["old"], limit=3)
        got = [r["id"] for r in rows]
        check("page: asked for an old one it reads pages back until that one is in, oldest first, and stops there",
              ids["old"] in got and got == sorted(got) and got[0] <= ids["old"] and len(got) > 3, str(got))
    finally:
        con.close()


def test_a_message_is_one_text_with_its_parts_in_order() -> None:
    """T438: a message is one Static, not one widget per part; this is what it says."""
    from handline.ui import chat_page as cp
    m = {"who": "backend", "when": 100, "rid": 7, "mid": 7, "part": 0, "text": "the body", "kind": "task",
         "tasks": [418], "task_title": "Fix it", "task_state": "queued", "reply_to": 3, "to": "reviewer"}
    t = cp.message_text(m, True, False, "them", {"success": "#00ff00"}).plain.split("\n")
    check("text: a task card is NEW TASK T<n> : title, then what happened; who and when are above it, not in it",
          t == ["NEW TASK T418 : Fix it", "assigned to reviewer"], str(t))
    reply = {**m, "kind": None, "tasks": [], "task_title": None, "task_state": None, "who": "you", "side": "you",
             "rid": 5934, "mid": 5934, "text": "applied?", "reply_to": 5928}
    check("text: a reply not picked is one line, the link and the text",
          cp.message_text(reply, False, False, "me").plain == "↩ #5928 applied?")
    picked = cp.message_text(reply, True, True, "me").plain
    lines = picked.split("\n")
    check("text: a reply picked is the head with #id, then the link and the text on one line",
          len(lines) == 2 and lines[0].startswith("you") and lines[0].endswith("#5934") and lines[1] == "↩ #5928 applied?",
          picked)
    check("text: an agent's reply is the same, head then link and text",
          cp.message_text({**reply, "who": "reviewer", "side": "agent"}, True, False, "them").plain.split("\n")[1]
          == "↩ #5928 applied?")
    plain = {**m, "kind": None, "tasks": [], "task_title": None}
    check("text: the head is left out until it is shown, and #id joins it when picked",
          "backend" not in cp.message_text(plain, False, False, "them").plain
          and "#7" in cp.message_text(plain, True, True, "them").plain.split("\n")[0])
    check("text: a note follows the body", cp.message_text({**m, "cancelled": True, "kind": None, "reply_to": None},
                                                            False, False, "me").plain.split("\n")[-1].startswith("cancelled"))


def test_a_reply_shows_a_link_to_what_it_answers() -> None:
    """T410 (founder #5295): messages stay in time order, unindented; a reply carries the
    number of its parent and draws a reply icon and that number, a click going to it."""
    from handline.ui import chat_page as cp
    from handline.infra import textsafe
    link = cp.reply_link(12)
    check("reply: the line is the icon and the parent's number, a click shows that message",
          link.plain == "↩ #12" and link.style.meta.get("@click") == "screen.show_message(12)", str(link.style.meta))
    check("reply: the line is inert text", not textsafe.has_unsafe(link.plain))
    check("reply: the answer row's number is the number a reply to an answer names",
          cp.mid_of({"rid": 5, "mid": 9}) == 9 and cp.mid_of({"rid": 5}) == 5)


def test_a_dismissed_request_leaves_no_dismissed_line_in_the_chat() -> None:
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("dismissline")
    agents.register("reviewer")
    con = db.connect()
    try:
        ok, _, rid = askmod.ask("human", "fyi: done", requester="reviewer")
        ok2, said = actions.dismiss(con, rid, actions.HUMAN, no_task="just an fyi")
        msgs = cp.messages_of(_page_rows(con, rid), "reviewer")
        check("chat: a dismissal is not drawn as a line, the ask itself stays",
              ok and ok2 and [m["part"] for m in msgs] == [0] and not any("dismissed" in m["text"] for m in msgs),
              str(msgs))
    finally:
        con.close()


def test_a_reply_closes_a_waiting_question_or_starts_a_new_request() -> None:
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("reply")
    agents.register("reviewer")
    con = db.connect()
    try:
        ok, _, rid = askmod.ask("human", "merge it?", requester="reviewer")
        check("reply: the agent's question is asked", ok)
        ok1, _ = cp.send_reply(con, "reviewer", rid, "yes")
        after = db.get_request(con, rid)
        check("reply: a reply to a waiting question is its verdict",
              ok1 and after["state"] == "landed" and db.shown_verdict(after) == "yes", str(dict(after)))
        ok2, _ = cp.send_reply(con, "reviewer", rid, "and again")
        child = db.get_request(con, rid + 1)
        check("reply: a second reply is a new request to the agent under it",
              ok2 and child is not None and child["parent_id"] == rid and child["target"] == "reviewer"
              and child["requester"] == actions.HUMAN)
        ok3, _, mine = askmod.ask("reviewer", "status?", requester=actions.HUMAN)
        con.execute("UPDATE requests SET state = 'landed' WHERE id = ?", (mine,))
        db.add_answer(con, mine, "fine")
        con.commit()
        ok4, _ = cp.send_reply(con, "reviewer", mine, "thanks, now ship it")
        check("reply: an answer to the human's own request is marked read, as the mailbox does",
              ok3 and ok4 and db.answer_row_of(con, mine)["delivered_at"], str(dict(db.get_request(con, mine))))
        _, _, theirs = askmod.ask("reviewer", "status again?", requester=actions.HUMAN)
        ok5, _ = cp.send_reply(con, "backend", theirs, "and the date?")
        kid5 = db.get_request(con, theirs + 1)
        check("reply: a reply from another agent's page (All chat) goes to the agent on that message, under it",
              ok5 and kid5["target"] == "reviewer" and kid5["parent_id"] == theirs, str(dict(kid5)))
        check("reply: a blank reply is refused", cp.send_reply(con, "reviewer", rid, "  ")[0] is False)
        check("reply: a reply to a request that is gone is refused",
              cp.send_reply(con, "reviewer", 99999, "x")[0] is False)
    finally:
        con.close()


def test_an_agents_queued_message_waits_below_the_line_without_buttons() -> None:
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("heldbelow")
    agents.register("reviewer")
    _, _, first = askmod.ask("reviewer", "hello", requester="backend")
    con = db.connect()
    try:
        con.execute("UPDATE requests SET delivered_at = ts WHERE id = ?", (first,))
        _, _, held = askmod.ask("reviewer", "second, not read yet", requester="backend")
        _, _, later = askmod.ask("backend", "asked after", requester="reviewer")
        con.execute("UPDATE requests SET ts = ts + 10 WHERE id = ?", (later,))
        con.commit()
        rows = cp.requests_page(con, "reviewer", True)[0]
    finally:
        con.close()
    msgs = cp.messages_of(rows, "reviewer")
    widgets = cp.transcript_widgets(msgs, False)
    texts = [w.m["text"] if isinstance(w, cp.ChatMessage) else "--" for w in widgets]
    check("chat: an undelivered message to the agent sits last; one the agent sent stays at its time (founder)",
          texts.index("second, not read yet") == len(texts) - 2 and texts[-1] == "--"
          and texts.index("hello") < texts.index("asked after") < texts.index("second, not read yet"),
          str(texts))
    check("chat: it has no Cancel / Send now buttons", not any(
        m["queued"] for m in msgs if m["text"] == "second, not read yet"))


def test_the_queue_reason_comes_from_the_message_below_the_line() -> None:
    """Auditor #5331: a held message the page's agent sent sits above the line and gives it no
    reason; the reason is the one of the message below the line."""
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("whyline")
    agents.register("reviewer")
    con = db.connect()
    try:
        askmod.ask("backend", "reviewer's own, not delivered", requester="reviewer")
        _, _, below = askmod.ask("reviewer", "to reviewer, not read", requester="backend")
        con.commit()
        msgs = cp.messages_of(cp.requests_page(con, "reviewer", True)[0], "reviewer")
        texts = [w.m["text"] if isinstance(w, cp.ChatMessage) else "--" for w in cp.transcript_widgets(msgs, False)]
        check("why: the one to the agent sits after the sent one",
              texts.index("reviewer's own, not delivered") < texts.index("to reviewer, not read"),
              str(texts))
        check("why: the reason is for the message to the agent, not the one sent",
              cp.wait_reason(con, "reviewer", msgs, False) == "reviewer: " + actions.queue_reason(con, "reviewer", below),
              cp.wait_reason(con, "reviewer", msgs, False))
    finally:
        con.close()


def test_a_message_between_two_other_agents_can_be_picked_in_all_chat() -> None:
    """Founder #5337: any message in All chat (also one from another agent) can be replied to, and a reply goes to its speaker."""
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("allpick")
    agents.register("reviewer")
    _, _, rid = askmod.ask("reviewer", "from another agent", requester="backend")
    con = db.connect()
    try:
        con.commit()
        msgs = cp.messages_of(cp.requests_page(con, "reviewer", True)[0], "reviewer")
        check("all chat: a message from another agent is replyable", msgs and all(m["replyable"] for m in msgs), str(msgs))
        cp.send_reply(con, "reviewer", rid, "a word from the founder")
        kid = db.get_request(con, rid + 1)
        check("all chat: a reply to it goes to its speaker, under it",
              kid["target"] == "backend" and kid["parent_id"] == rid, str(dict(kid)))
    finally:
        con.close()


def test_a_number_in_a_message_becomes_a_link_to_it() -> None:
    from handline.ui import chat_page as cp
    from rich.text import Text
    def targets(s):
        t = cp.link_refs(Text(s))
        return [sp.style.meta.get("@click") for sp in t.spans]
    check("refs: each #id is a link to show_message",
          targets("see #12 and #7") == ["screen.show_message(12)", "screen.show_message(7)"], str(targets("see #12 and #7")))
    check("refs: a#3 and ##4 are not links", targets("a#3") == [] and targets("##4") == [], str((targets("a#3"), targets("##4"))))


def test_a_task_ref_in_a_message_is_found_only_when_it_stands_alone() -> None:
    found = lambda s: [t for _, _, t in actions.task_refs_in(s)]  # noqa: E731
    for s in ("T835", "see T835 now", "(T835)", "T835,", "T835.", "a T835: b", "T835를", "[T835]", "x\nT835"):
        check(f"task ref: {s!r} is one", found(s) == [835], str(found(s)))
    for s in ("LIT123", "XT5", "T835a", "T12_3", "t835", "T", "T 835", "_T835", "9T835", "#T835",
              "http://x.io/T835", "see `T835` here", "https://a.b/c?T835"):
        check(f"task ref: {s!r} is plain", found(s) == [], str(found(s)))
    check("task ref: two in a line", found("T1 and T22") == [1, 22])
    check("task ref: the span is the token", actions.task_refs_in("go T835!") == [(3, 7, 835)])


async def test_clicking_a_task_ref_in_a_message_opens_that_task_and_a_missing_one_is_refused() -> None:
    from handline.team import ask as askmod
    from handline.ui.chat_page import TaskDetail
    _home("taskref")
    con = db.connect()
    try:
        ok, said, tid = actions.task_new("product", "backend", "Fix it", "the work and its done")
    finally:
        con.close()
    check("task ref: the task was made", ok and tid, str(said))
    ref = actions.task_ref(tid)
    askmod.say("human", f"see {ref} and T99999 and LIT123", requester="backend")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause(0.5)
        page = app.screen
        check("task ref: the chat page is open", isinstance(page, ChatPage), type(page).__name__)
        msg = next(w for w in page.query(cp_ChatMessage) if "T99999" in str(w._text.render()))
        links = [sp.style.meta["@click"] for sp in msg._text.render().spans if sp.style.meta.get("@click")]
        check("task ref: the two T<n> are links, LIT123 is not",
              links == [f"screen.open_task({tid})", "screen.open_task(99999)"], str(links))
        def at(word: str) -> tuple[int, int]:
            before = msg._text.render().plain[:msg._text.render().plain.index(word)]
            return len(before.rsplit("\n", 1)[-1]), before.count("\n")

        check("task ref: nothing is picked at first", page.replying is None, str(page.replying))
        await pilot.click(msg._text, offset=at("T99999"))
        await pilot.pause(0.3)
        check("task ref: an unknown task opens no page", app.screen is page, type(app.screen).__name__)
        check("task ref: and the click did not pick the message", page.replying is None, str(page.replying))
        await pilot.click(msg._text, offset=at(ref))
        await pilot.pause(0.3)
        check("task ref: a click on the ref opens that task's page",
              isinstance(app.screen, TaskDetail) and app.screen.tid == tid, type(app.screen).__name__)
        await pilot.press("escape")
        await pilot.pause(0.2)
        await pilot.click(msg._text, offset=at("LIT123"))
        await pilot.pause(0.2)
        check("task ref: a click on plain text still picks the message", page.replying is msg, str(page.replying))


def test_a_file_path_in_a_message_becomes_a_link_that_opens_it() -> None:
    """~/ and absolute .md paths (non-ASCII, @, digits, hyphens) are links, the sentence's
    comma, period and bracket are not part of them, and what only looks like a path is not one."""
    from textual.actions import parse
    from handline.ui import chat_page as cp
    from rich.text import Text
    founder = "~/notes/@work/café/Plan-2nd-draft.md"
    def found(s):
        return [s[h.start:h.end] for h in actions.find_paths(s)]
    check("paths: the founder's example, then a comma", found(f"{founder}, /~~/.md later") == [founder, "/~~/.md"],
          str(found(f"{founder}, /~~/.md later")))
    check("paths: a closing bracket or a full stop ends it", found("(see /Users/a/b-2.md). Next") == ["/Users/a/b-2.md"],
          str(found("(see /Users/a/b-2.md). Next")))
    check("paths: quoted ones may hold spaces", found("`/Users/r/My Docs/a b.md` and \"~/x y/z\"") == ["/Users/r/My Docs/a b.md", "~/x y/z"])
    check("paths: path:line is one link, open gets the path",
          [(h.path, h.line) for h in actions.find_paths("at /tmp/f.py:42:7.")] == [("/tmp/f.py", 42)])
    check("paths: a directory under ~/ is a link", found("in ~/dir/ now") == ["~/dir/"])
    check("paths: fractions, dates, URLs, slash commands and relative paths are not",
          found("1/2 2026/10/01 and/or https://x.com/a/b.md /loop handline/a.py ./a.md") == [])
    with tempfile.TemporaryDirectory() as d:
        Path(d, "docs").mkdir()
        Path(d, "docs", "x.md").write_text("x")
        def rel(s):
            return [(s[h.start:h.end], h.path) for h in actions.find_paths(s, [d])]
        check("paths tier 2: a relative path that exists links, to its full path",
              rel("see docs/x.md, and x.md") == [("docs/x.md", f"{d}/docs/x.md")], str(rel("see docs/x.md, and x.md")))
        check("paths tier 2: one that does not exist stays plain, quoted or not",
              rel("docs/nope.md a/b `docs/nope.md` `1/2` `and/or`") == [], str(rel("docs/nope.md a/b `docs/nope.md` `1/2` `and/or`")))
        check("paths tier 2: a backticked path:line links to the full path and its line",
              [(h.path, h.line) for h in actions.find_paths("at `docs/x.md:7` and \"docs/x.md:9\"", [d])]
              == [(f"{d}/docs/x.md", 7), (f"{d}/docs/x.md", 9)])
        click = lambda t: [sp.style.meta["@click"] for sp in t.spans if "@click" in (getattr(sp.style, "meta", None) or {})]
        orig = actions.path_bases
        actions.path_bases = lambda who: [d]
        try:
            m = {"text": "see docs/x.md now", "who": "a", "when": 0, "side": "agent"}
            check("paths: a message body carries the open_path click", click(cp.message_parts(m)[1]) == [f"app.open_path('{d}/docs/x.md')"])
            e = {"kind": "memo", "ts": 0, "who": "a", "text": "docs/x.md", "edited_at": None, "memo": 1}
            check("paths: a timeline line carries it", len(click(cp.timeline_text(e))) == 1)
            check("paths: a card note carries it", click(cp.card_lines({"tasks": [1], "to": "b", "who": "a", "note": "docs/x.md"}))[-1:] == [f"app.open_path('{d}/docs/x.md')"])
            pg = cp.task_page({"id": 1, "title": "t", "state": "doing", "owner": "a", "opened_by": "a", "created_at": 0, "parent": None,
                               "done": None, "discarded": None, "merged_into": None, "asker": "a", "brief": "docs/x.md",
                               "children": [], "timeline": []})
            check("paths: a task's brief carries it", len(click(pg["brief"])) == 1)
        finally:
            actions.path_bases = orig
    ok, said = actions.open_path("docs/x.md")
    check("paths: a relative path is never opened from the bridge's own cwd", not ok and "not found" in said, said)
    t = cp.link_paths(Text(f"see {founder}, ok"))
    click = t.spans[0].style.meta["@click"]
    check("paths: the click names the app's open_path with the whole path",
          parse(click) == ("app", "open_path", (founder,)), click)
    ok, said = actions.open_path("/no/such/dir/x.md")
    check("paths: opening what is not there says so", not ok and "not found" in said, said)


def test_the_task_title_is_cut_to_the_room_beside_the_other_columns() -> None:
    """Founder #5337: the title column takes what is left of the table, so every row shows."""
    from handline.ui.components import tasks_view as tv
    check("clip: a short title is left alone", tv.clip("short", 10) == "short")
    check("clip: a long one ends in … within the room", tv.clip("a very long task title", 10) == "a very lo…"
          and len(tv.clip("a very long task title", 10)) == 10, tv.clip("a very long task title", 10))
    check("clip: wide (Korean) characters are counted by their cells", tv.clip("가나다라마바사", 8) == "가나다…")
    check("room: the table minus the other columns and their padding",
          tv.title_room(100, [3, 5, 8]) == 100 - (5 + 7 + 10) - 4, str(tv.title_room(100, [3, 5, 8])))
    check("room: never below the minimum", tv.title_room(20, [10, 10]) == tv.MIN_TITLE)


def test_a_verb_that_only_writes_the_record_runs_on_a_thread_not_a_process() -> None:
    """Founder #5401: dismissing was slow because every verb started an interpreter. A verb
    with nothing to wait for (dismiss) runs in this process and is collected the same way."""
    import time
    from handline.team import ask as askmod
    from handline.ui import jobs as jobsmod
    _home("inlinejob")
    agents.register("reviewer")
    _, _, rid = askmod.ask("human", "a question?", requester="reviewer")
    jobs = jobsmod.Jobs()
    check("jobs: dismiss starts", jobs.start("dismiss", f"#{rid}", "dismissing", (rid,), {"me": actions.HUMAN}) == "")
    check("jobs: it is not a process", isinstance(jobs.running[0].proc, jobsmod.InlineProc)
          and jobs.running[0].proc.pid is None)
    said = []
    for _ in range(100):
        said = jobs.reap()
        if said:
            break
        time.sleep(0.02)
    con = db.connect()
    try:
        check("jobs: it is collected, and the row is dismissed",
              said and said[0][0] and db.get_request(con, rid)["state"] == "landed", str(said))
    finally:
        con.close()
    check("jobs: a verb that waits on something is still a process of its own",
          actions.WAITS["wake"][0] != "" and actions.WAITS["dismiss"][0] == "")


def test_the_tasks_of_an_agent_are_what_it_owns_or_opened() -> None:
    from handline.ui import chat_page as cp
    _home("tasks")
    agents.register("reviewer")
    con = db.connect()
    try:
        a = db.new_task(con, "bravo", "backend", "orcapipe")
        b = db.new_task(con, "alpha", "backend", "zed")
        c = db.new_task(con, "opened by it", "orcapipe", "backend")
        db.new_task(con, "someone else's", "orcapipe", "orcapipe")
        closed = db.new_task(con, "closed", "backend", "orcapipe")
        con.execute("UPDATE tasks SET discarded_by = 'human' WHERE id = ?", (closed,))
        con.execute("UPDATE tasks SET created_at = created_at - 5000 WHERE id = ?", (a,))
        con.commit()
        rows = cp.tasks_of(con, "backend")
        check("tasks: what it owns or opened, not others', not discarded",
              sorted(r[0] for r in rows) == sorted([a, b, c]), str([r[0] for r in rows]))
        check("tasks: the role is owns or opened",
              {r[0]: r[1] for r in rows} == {a: "owns", b: "owns", c: "opened"})
        check("tasks: the other party is shown",
              {r[0]: r[4] for r in rows} == {a: "orcapipe", b: "zed", c: "orcapipe"})
        actions.chat_reply(con, "reviewer", "touch")
        db.link_request(con, a, actions.chat_parent(con, "reviewer"))
        newer = {r[0]: r[5] for r in cp.tasks_of(con, "backend")}
        check("tasks: a linked request is a change to the task", newer[a] > {r[0]: r[5] for r in rows}[a])
    finally:
        con.close()


def test_batch_results_are_one_toast_for_what_worked_and_one_for_the_refusals() -> None:
    from handline.ui.components.tasks_view import result_notes
    got = result_notes([(14, True, "x"), (15, True, "y"), (16, False, "has a child")], "discard")
    check("results: successes in one toast, the refusals in another with the reason",
          got == [("discarded 2: T14 T15", "information"), ("T16: has a child", "error")], got)


def test_the_task_page_offers_discard_only_on_the_founders_waiting_task() -> None:
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("discardable")
    agents.register("reviewer")
    con = db.connect()
    try:
        mine = db.new_task(con, "mine", "backend", actions.HUMAN)
        theirs = db.new_task(con, "theirs", "backend", "orcapipe")
        done = db.new_task(con, "done", "backend", actions.HUMAN)
        con.execute("UPDATE tasks SET done_evidence = 'x', done_at = 1 WHERE id = ?", (done,))
        doing = db.new_task(con, "doing", "backend", actions.HUMAN)
        ok, _, rid = askmod.ask("reviewer", "work on it", requester=actions.HUMAN)
        db.link_request(con, doing, rid)
        con.commit()
        can = {tid: cp.task_detail(con, tid)["can_discard"] for tid in (mine, theirs, done, doing)}
        check("discard: offered on any waiting or doing task, whoever asked for it, not on one done",
              can == {mine: True, theirs: True, done: False, doing: True}, str(can))
        check("discard: the doing task is the one with a request queued",
              ok and cp.task_detail(con, doing)["state"] == "doing")
        ok, said = actions.discard_task(con, mine, actions.HUMAN, "not needed")
        check("discard: the button's action discards it", ok, said)
        check("discard: and a discarded task is not offered again", not cp.task_detail(con, mine)["can_discard"])
        ok, said = actions.discard_task(con, doing, actions.HUMAN, "stop")
        check("discard: a doing task is refused without stop, its request still open", not ok and "request open" in said, said)
        ok, said = actions.discard_task(con, doing, actions.HUMAN, "changed my mind", stop=True)
        req = db.get_request(con, rid)
        check("discard: with stop the request is cancelled and the task discarded",
              ok and req["state"] == "withdrawn"      # never delivered: withdrawn, nobody is told
              and cp.task_detail(con, doing)["state"] == "discarded", f"{said} / {dict(req)}")
        other = db.new_task(con, "asked by an agent", "reviewer", "orcapipe")
        _, _, rid2 = askmod.ask("reviewer", "work", requester="orcapipe")
        db.link_request(con, other, rid2)
        ok, said = actions.discard_task(con, other, "orcapipe", "x", stop=True)
        check("discard: stop is the founder's; an agent asking for its own task cannot use it, the request stays",
              not ok and db.get_request(con, rid2)["state"] == "queued" and "request open" in said, said)
        held = db.new_task(con, "started, not delivered", "reviewer", actions.HUMAN)
        _, _, rid3 = askmod.ask("reviewer", "start it", requester=actions.HUMAN)
        db.link_request(con, held, rid3)
        db.set_current_task(con, "reviewer", held)
        ok, said = actions.discard_task(con, held, actions.HUMAN, "no", stop=True)
        told = con.execute("SELECT body FROM requests WHERE target = 'reviewer' AND body LIKE ?",
                           (f"T{held} discarded%stop work%",)).fetchall()
        check("discard: an owner who already holds it (task start) is told to stop, though nothing was delivered",
              ok and len(told) == 1, said)
        _, _, chat = askmod.ask("reviewer", "look into the retry", requester=actions.HUMAN)
        askmod.resolve(chat, "on it")
        db.set_current_task(con, "reviewer", None)      # the earlier tasks of this test held it
        ok, said, begun = actions.task_begin(con, "reviewer", "retry", chat, "the founder asked")
        check("discard: a task begun on a founder message is doing with no request open", ok and
              cp.task_detail(con, begun)["state"] == "doing", said)
        ok, said = actions.discard_task(con, begun, actions.HUMAN, "no", stop=True)
        told = con.execute("SELECT body FROM requests WHERE target = 'reviewer' AND body LIKE ?",
                           (f"T{begun} discarded%stop work%",)).fetchall()
        check("discard: and discarding it with stop tells its owner exactly once", ok and len(told) == 1,
              f"{said} {len(told)}")
        given = db.new_task(con, "handed to the founder", actions.HUMAN, "reviewer")
        other_given = db.new_task(con, "someone else's, an agent's", "backend", "reviewer")
        check("discard T660: offered on a task assigned to the founder and on an agent's own",
              cp.task_detail(con, given)["can_discard"] and cp.task_detail(con, other_given)["can_discard"])
        ok, said = actions.discard_task(con, given, actions.HUMAN, "not needed")
        told = con.execute("SELECT body FROM requests WHERE target = 'reviewer' AND body LIKE ?",
                           (f"T{given} discarded by the founder: not needed",)).fetchall()
        check("discard T660: the opener is told once, by the founder, with the reason", ok and len(told) == 1,
              f"{said} {len(told)}")
        ok, said = actions.discard_task(con, other_given, actions.HUMAN, "not needed either")
        told = {tgt: con.execute("SELECT COUNT(*) FROM requests WHERE target = ? AND body LIKE ?",
                                 (tgt, f"T{other_given} discarded by the founder: not needed either")).fetchone()[0]
                for tgt in ("backend", "reviewer")}
        check("discard T660: the founder discarding an agent-to-agent task tells its owner and its opener once each",
              ok and told == {"backend": 1, "reviewer": 1}, f"{said} {told}")
        own = db.new_task(con, "its own", "reviewer", "reviewer")
        ok, said = actions.discard_task(con, own, "reviewer", "mine, gone")
        check("discard T660: an opener discarding its own task is told nothing", ok and not con.execute(
            "SELECT 1 FROM requests WHERE target = 'reviewer' AND body LIKE ?", (f"T{own} discarded%",)).fetchone(), said)
    finally:
        con.close()


def test_the_settings_form_checks_and_saves_only_what_changed() -> None:
    from handline.ui import chat_page as cp
    home = _home("settings")
    cfg = cp.settings_of("backend")
    check("settings: the agent file is read", cfg["effort"] in agents.VALID_EFFORT
          and cfg["model"] and cfg["weight"] in cp.WEIGHTS, str(cfg))
    form = {k: cfg[k] for k in cp.EDITABLE}
    check("settings: an untouched form has no changes", cp.settings_changes(cfg, form) == [])
    check("settings: a blank repo means leave the one it has",
          cp.settings_changes({**cfg, "repo": "/somewhere"}, {**form, "repo": " "}) == [])
    check("settings: a changed field is reported old to new",
          cp.settings_changes(cfg, {**form, "model": "opus"}) == [("model", cfg["model"], "opus")])
    check("settings: a good form has no problem", cp.settings_problem({**form, "repo": ""}) is None)
    for name, bad in (("empty model", {"model": " "}), ("control character in model", {"model": "a\x1bb"}),
                      ("yaml would not read it as text", {"model": "no"}), ("unknown effort", {"effort": "loud"}),
                      ("unknown weight", {"weight": "huge"}), ("repo that is not a folder", {"repo": "/no/such/dir"})):
        check(f"settings: refused - {name}", cp.settings_problem({**form, "repo": "", **bad}) is not None)
    repo = home / "work"
    repo.mkdir()
    changes = cp.settings_changes(cfg, {**form, "model": "opus", "effort": "high", "repo": str(repo)})
    ok, said = cp.save_settings("backend", changes)
    now = agents.read("backend")
    check("settings: a save writes the changed fields", ok and now["model"] == "opus"
          and now["effort"] == "high" and now["repo"] == str(repo), said)
    check("settings: and leaves the rest of the file as it was",
          now["weight"] == cfg["weight"] and now.get("enabled") is True)
    agents.register("backend", weight="heavy")
    ok, said = cp.save_settings("backend", [("effort", now["effort"], "low")])
    check("settings: a save leaves a field changed since the tab was read",
          ok and agents.read("backend")["weight"] == "heavy", said)
    check("settings: skip prompts reads off for an agent that never set it", cfg["skip_permissions"] == "off")
    check("settings: skip prompts must be on or off",
          cp.settings_problem({**form, "repo": "", "skip_permissions": "yes"}) is not None)
    ok, said = cp.save_settings("backend", cp.settings_changes(cfg, {**form, "skip_permissions": "on"}))
    check("settings: turning skip prompts on writes it and reads back on",
          ok and agents.skip_permissions(agents.read("backend")) and cp.settings_of("backend")["skip_permissions"] == "on", said)
    check("settings: and the rest of the file is as it was", agents.read("backend")["weight"] == "heavy")
    ok, said = cp.save_settings("backend", [("skip_permissions", "on", "off")])
    check("settings: turning it off removes it", ok and not agents.skip_permissions(agents.read("backend")), said)
    with open(agents.agent_file("backend"), "a") as f:
        f.write("skip_permissions: true\n")
    t.reseal(agents.agent_file("backend"))
    unhinted = cp.settings_of("backend")
    check("settings: a key in agent.yaml with no grant shows off, as the launch ignores it",
          agents.skip_permissions(agents.read("backend")) and not agents.skip_permissions_granted("backend")
          and unhinted["skip_permissions"] == "off", str(unhinted["skip_permissions"]))
    ok, said = cp.save_settings("backend", cp.settings_changes(unhinted, {**form, "skip_permissions": "on"}))
    check("settings: saving on then grants it", ok and agents.skip_permissions_granted("backend")
          and cp.settings_of("backend")["skip_permissions"] == "on", said)


def test_the_browser_setting_writes_chrome_and_the_sites_through_register() -> None:
    from handline.ui import chat_page as cp
    _home("browser-setting")
    cfg = cp.settings_of("backend")
    form = {k: cfg[k] for k in cp.EDITABLE}
    check("browser: an agent that never set it reads off, with no sites",
          cfg["browser"] == "off" and cfg["browser_sites"] == "", str(cfg["browser"]))
    ok, said = cp.save_settings("backend", cp.settings_changes(cfg, {**form, "browser": "chrome"}))
    check("browser: Chrome writes chrome in agent.yaml and reads back",
          ok and agents.chrome(agents.read("backend")) and cp.settings_of("backend")["browser"] == "chrome", said)
    check("browser: Chrome has no site list, so typed sites are not a change",
          cp.settings_changes(cp.settings_of("backend"), {**form, "browser": "chrome", "browser_sites": "https://a.com"}) == [])
    check("browser: Playwright needs a site",
          cp.settings_problem({**form, "repo": "", "browser": "playwright", "browser_sites": " "}) is not None)
    for bad in ("javascript:x", 'https://a.com" --evil', "https://a.com;rm", "ftp://a.com"):
        check(f"browser: refused site {bad!r}",
              cp.settings_problem({**form, "repo": "", "browser": "playwright", "browser_sites": bad}) is not None)
    cur = cp.settings_of("backend")
    ok, said = cp.save_settings("backend", cp.settings_changes(
        cur, {**form, "browser": "playwright", "browser_sites": "https://a.com, https://b.org:8080/"}))
    now = agents.read("backend")
    check("browser: Playwright builds the browser server with those origins and drops chrome",
          ok and not agents.chrome(now) and agents.browser_mode(now) == "playwright"
          and agents.browser_origins(now) == ["https://a.com", "https://b.org:8080"], said)
    check("browser: its command keeps the token read and the default profile",
          f'PLAYWRIGHT_MCP_EXTENSION_TOKEN=$(cat {agents.paths.home() / "browser" / "token"})' in agents._browser_command(now)
          and f'--profile-dir-name "{agents.DEFAULT_BROWSER_PROFILE}"' in agents._browser_command(now))
    cur = cp.settings_of("backend")
    ok, said = cp.save_settings("backend", cp.settings_changes(cur, {**form, "browser": "playwright", "browser_sites": "https://c.net"}))
    check("browser: editing the list alone replaces the origins",
          ok and agents.browser_origins(agents.read("backend")) == ["https://c.net"], said)
    # another MCP server and a custom profile survive; off removes only the browser one
    import json
    cfgd = agents.read("backend")
    other = json.loads(cfgd["mcp_config"])
    other["mcpServers"]["other"] = {"command": "x"}
    other["mcpServers"]["browser"]["args"][1] = other["mcpServers"]["browser"]["args"][1].replace("Profile 17", "Profile 3")
    agents.register("backend", mcp_config=json.dumps(other))
    cur = cp.settings_of("backend")
    cp.save_settings("backend", cp.settings_changes(cur, {**form, "browser": "playwright", "browser_sites": "https://d.io"}))
    now = agents.read("backend")
    check("browser: a custom profile and other servers are kept",
          '--profile-dir-name "Profile 3"' in agents._browser_command(now)
          and "other" in json.loads(now["mcp_config"])["mcpServers"], str(now["mcp_config"]))
    cur = cp.settings_of("backend")
    cp.save_settings("backend", cp.settings_changes(cur, {**form, "browser": "off"}))
    now = agents.read("backend")
    check("browser: off removes the browser server and chrome, keeps the other server",
          agents.browser_mode(now) == "off" and not agents.chrome(now)
          and list(json.loads(now["mcp_config"])["mcpServers"]) == ["other"], str(now.get("mcp_config")))
    hand = json.dumps({"mcpServers": {"browser": {"command": "npx", "args": ["playwright-mcp", "--headless"]}}})
    agents.register("backend", mcp_config=hand)
    cur = cp.settings_of("backend")
    form = {k: cur[k] for k in cp.EDITABLE}
    check("browser: a hand-made server with no site list reads as custom, with no sites",
          cur["browser"] == "custom" and cur["browser_sites"] == "", str(cur["browser"]))
    check("browser: it blocks no unrelated save", cp.settings_problem({**form, "repo": ""}) is None)
    ok, said = cp.save_settings("backend", cp.settings_changes(cur, {**form, "model": "opus"}))
    check("browser: a model change saves and leaves the server as it was",
          ok and agents.read("backend")["mcp_config"] == agents.mcp_config_text(hand), said)
    check("browser: custom is no change, and its sites box is not read",
          cp.settings_changes(cur, {**form, "browser_sites": "https://x.com"}) == [])
    ok, said = cp.save_settings("backend", [("browser", "off", "custom")])
    check("browser: custom cannot be chosen as a mode", not ok and agents.read("backend")["mcp_config"] == agents.mcp_config_text(hand), said)
    ok, said = cp.save_settings("backend", cp.settings_changes(cur, {**form, "browser": "chrome"}))
    check("browser: picking Chrome turns it on and replaces the hand-made server",
          ok and agents.chrome(agents.read("backend")) and not agents.read("backend").get("mcp_config"), said)
    agents.register("backend", chrome=False, mcp_config=hand)
    ok, said = cp.save_settings("backend", cp.settings_changes(cp.settings_of("backend"), {**form, "browser": "off"}))
    check("browser: picking off removes the hand-made server",
          ok and agents.browser_mode(agents.read("backend")) == "off" and not agents.read("backend").get("mcp_config"), said)
    agents.register("backend", mcp_config=hand)
    ok, said = cp.save_settings("backend", [("browser", "off", "playwright"), ("browser_sites", "", "https://z.org")])
    check("browser: picking Playwright is what replaces it",
          ok and agents.browser_origins(agents.read("backend")) == ["https://z.org"], said)
    from handline.infra import protected
    import os
    old = os.environ.get("OCP_AGENT")
    os.environ["OCP_AGENT"] = "backend"
    try:
        ok, said = cp.save_settings("backend", [("browser", "off", "chrome")])
    finally:
        if old is None:
            os.environ.pop("OCP_AGENT")
        else:
            os.environ["OCP_AGENT"] = old
    check("browser: an agent session cannot write it (founder-only, same register path)",
          not ok and not agents.chrome(agents.read("backend")), said)


async def test_the_hr_tab_edits_any_agents_settings_and_shows_a_settings_proposal() -> None:
    import html
    from textual.widgets import DataTable, Input, Select
    from handline.ui.components import proposals as pr
    from handline.ui.components.agent_settings_screen import AgentSettingsScreen
    _home("hr-edit")
    ok, said = hr.propose("settings", "backend", requester="backend", why="it reads the docs",
                          sets={"browser": "playwright", "browser_sites": "https://docs.example.org"})
    check("hr edit: a settings proposal is raised", ok, said)
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause(0.3)
        app.screen.query_one("#scope").active = "hr"
        await pilot.pause(0.5)
        table = app.screen.query_one("#hr_agents", DataTable)
        check("hr edit: every agent is listed", table.row_count >= 1
              and "backend" in [table.coordinate_to_cell_key((i, 0)).row_key.value for i in range(table.row_count)])
        check("hr edit: the settings proposal shows what it would set",
              "set browser_sites" in pr.describe({**hr.listing()[0], "rid": 1, "asked": 0, "status": pr.DECIDE}).plain)
        Path(os.environ.get("HR_TAB_SHOT", os.devnull)).write_text(app.export_screenshot())
        table.focus()
        await pilot.press("e")
        await pilot.pause(0.5)
        check("hr edit: e opens the same settings form on that agent",
              isinstance(app.screen, AgentSettingsScreen) and app.screen.query_one("#s_model", Input).value == agents.read("backend")["model"])
        app.screen.query_one("#s_effort", Select).value = "high"
        await pilot.pause(0.6)
        check("hr edit: a select saves the moment it changes, with no question asked",
              agents.read("backend")["effort"] == "high" and isinstance(app.screen, AgentSettingsScreen), str(agents.read("backend")))
        check("hr edit: and says so beside it", "saved" in str(app.screen.query_one("#n_s_effort").render()))
        Path(os.environ.get("HR_SHOT", os.devnull)).write_text(app.export_screenshot())
        await pilot.press("escape")
        await pilot.pause(0.4)
        check("hr edit: escape is back on the HR tab", not isinstance(app.screen, AgentSettingsScreen))


async def test_the_settings_page_shows_the_browser_mode_and_enables_sites_only_for_playwright() -> None:
    import html
    from handline.ui import chat_page as cp
    from textual.widgets import Button, Input, Select, Static
    from handline.ui.components.sites_list import SitesList, SiteEdit
    _home("browser-page")
    app = bt.BridgeApp()
    async with app.run_test(size=(110, 50)) as pilot:
        page = cp.ChatPage("backend")
        await app.push_screen(page)
        await pilot.pause()
        page.query_one("Tabs").active = "settings"
        await pilot.pause(0.4)
        mode, sites = page.query_one("#s_browser", Select), page.query_one("#s_browser_sites", SitesList)
        check("browser page: it starts off and the site list is greyed out", mode.value == "off" and sites.disabled)
        mode.value = "chrome"
        await pilot.pause(0.2)
        check("browser page: Claude in Chrome has no site list either", sites.disabled)
        mode.value = "playwright"
        await pilot.pause(0.2)
        check("browser page: sandboxed Playwright opens the site list", not sites.disabled)
        async def type_site(text):
            box = app.screen.query_one("#se_site")
            box.value = text
            await pilot.press("enter")
            await pilot.pause(0.2)
        sites.set_sites([])
        await pilot.pause(0.2)
        check("browser page: an empty list says none", "none" in _screen_text(page))
        sites.query_one("#st_add").press()
        await pilot.pause(0.3)
        check("browser page: Add opens the site box", isinstance(app.screen, SiteEdit))
        await type_site("ftp://nope.com")
        check("browser page: a site that is not an origin is refused and the box stays",
              isinstance(app.screen, SiteEdit) and sites.sites == [])
        await type_site("https://news.ycombinator.com/")
        check("browser page: a good site is added, trailing slash dropped",
              isinstance(app.screen, cp.ChatPage) and sites.sites == ["https://news.ycombinator.com"], str(sites.sites))
        sites.query_one("#st_add").press()
        await pilot.pause(0.3)
        await type_site("HTTPS://NEWS.ycombinator.com")
        check("browser page: a duplicate is refused", isinstance(app.screen, SiteEdit) and sites.sites == ["https://news.ycombinator.com"])
        await type_site("https://www.google.com")
        check("browser page: a second site is a second row", sites.sites == ["https://news.ycombinator.com", "https://www.google.com"]
              and sites.query_one("#st_ed_1", Button) is not None)
        sites.query_one("#st_ed_0").press()
        await pilot.pause(0.3)
        check("browser page: edit opens on the site", app.screen.query_one("#se_site").value == "https://news.ycombinator.com")
        await type_site("https://www.google.com")
        check("browser page: an edit into another row's site is refused", isinstance(app.screen, SiteEdit))
        await type_site("https://old.reddit.com")
        check("browser page: an edit replaces that row", sites.sites == ["https://old.reddit.com", "https://www.google.com"], str(sites.sites))
        sites.query_one("#st_rm_0").press()
        await pilot.pause(0.2)
        check("browser page: delete removes that row", sites.sites == ["https://www.google.com"], str(sites.sites))
        sites.set_sites(["https://news.ycombinator.com", "https://www.google.com"])
        await pilot.pause(0.2)
        check("browser page: the list is as tall as its rows, so what follows it is not pushed away",
              sites.region.height == 3 and page.query_one("#s_idle_close").region.y == sites.region.y + 4, str(sites.region))
        await pilot.pause(0.4)
        check("browser page: the mode and the sites were saved as they changed, with no Save",
              agents.browser_mode(agents.read("backend")) == "playwright"
              and agents.browser_origins(agents.read("backend")) == ["https://www.google.com"], str(agents.read("backend")))
        shown = html.unescape(re.sub(r"<[^>]*>", "", app.export_screenshot())).replace("\xa0", " ")
        Path(os.environ.get("BROWSER_SHOT", os.devnull)).write_text(app.export_screenshot())
        check("browser page: the section names itself and the next-launch note", "from the next launch" in shown, shown[-400:])
        await pilot.press("escape")


def test_a_message_sits_where_the_agent_read_it_and_a_line_ahead_of_the_queue() -> None:
    from handline.ui import chat_page as cp
    def row(i, ts, delivered, requester=actions.HUMAN):
        return {"id": i, "ts": ts, "resolved_at": None, "requester": requester, "target": "reviewer",
                "state": "queued", "body": f"m{i}", "answer": None, "dismisser": None,
                "dismiss_reason": None, "delivered_at": delivered, "typed_at": delivered,
                "landed_at": delivered, "landing_tries": None, "parent_id": None,
                "kind": None, "tasks": None, "task_title": None, "task_state": None}
    msgs = cp.messages_of([row(1, 100, 300), row(2, 200, 250), row(3, 150, None)], "reviewer")
    check("chat: yours are ordered by when the agent read them, unread ones last",
          [m["text"] for m in msgs] == ["m2", "m1", "m3"], str([m["text"] for m in msgs]))
    between = cp.messages_of([row(1, 100, 400, "alpha"), row(2, 200, 300, "beta"), row(3, 150, None, "alpha")],
                             "reviewer")
    check("chat: agents' messages to each other too, by when they were delivered, undelivered last",
          [m["text"] for m in between] == ["m2", "m1", "m3"], str([m["text"] for m in between]))
    kinds = [type(w).__name__ for w in cp.transcript_widgets(msgs, True)]
    check("chat: no line between the messages; one spare row ends the chat",
          kinds == ["ChatMessage", "ChatMessage", "ChatMessage", "WorkLine"], str(kinds))
    widgets = cp.transcript_widgets(msgs, False, wait="waiting for x's reply (#1)")
    check("chat: what the agent waits on is drawn on the spare row, where working would be",
          "waiting for x's reply (#1)" in widgets[-1].render().plain, str(widgets[-1].render().plain))
    check("chat: a message still sending says why; a delivered one does not",
          cp.landing_label("queued", "behind #9") == "sending · behind #9"
          and cp.landing_label("delivered", "behind #9") == cp.LANDING_LABELS["delivered"])


def test_the_chat_says_typed_until_the_transcript_shows_the_line() -> None:
    """T418: a request typed at an agent is delivered only once its transcript shows it."""
    from handline.ui import chat_page as cp
    now = int(time.time())
    def row(typed, landed, tries=None):
        return {"id": 1, "ts": 100, "resolved_at": None, "requester": actions.HUMAN, "target": "reviewer",
                "state": "queued", "body": "m", "answer": None, "dismisser": None,
                "dismiss_reason": None, "delivered_at": typed, "typed_at": typed, "landed_at": landed,
                "landing_tries": tries}
    def label_and_buttons(r):
        m = cp.messages_of([r], "reviewer")[0]
        kids = cp.ChatMessage(m)._pending_children
        strip = [c for c in kids if type(c).__name__ == "Horizontal"][0]
        texts = [str(c.content) for c in strip._pending_children
                 if type(c).__name__ == "Static"]
        return m["landing"], texts, [c for c in strip._pending_children if type(c).__name__ == "Button"]
    check("chat: not typed yet reads queued, with send now and cancel",
          label_and_buttons(row(None, None))[0] == "queued" and len(label_and_buttons(row(None, None))[2]) == 2)
    landing, texts, buttons = label_and_buttons(row(now, None))
    check("chat: typed but not in the transcript reads typed, and cannot be sent now",
          landing == "typed" and texts == ["sending"] and not buttons, str((landing, texts)))
    landing, texts, _ = label_and_buttons(row(now - 1000, None, 2))
    check("chat: typed again and again with nothing in the transcript still reads sending - there is no giving up",
          landing == "typed" and texts == ["sending"], str((landing, texts)))
    landing, texts, _ = label_and_buttons(row(now, now))
    check("chat: the transcript shows it: delivered", landing == "delivered"
          and texts == ["delivered, not answered yet"], str((landing, texts)))


def test_a_delivery_changes_the_chat_signature() -> None:
    from handline.ui import chat_page as cp
    _home("sigdeliver")
    con = db.connect()
    try:
        rid = db.add_request(con, "alpha", "reviewer", "one", None, None, None, None, 0, None)
        con.commit()
        before = cp.signature(con, "reviewer", True)
        con.execute("UPDATE requests SET delivered_at = 5, typed_at = 5 WHERE id = ?", (rid,))
        con.commit()
        typed = cp.signature(con, "reviewer", True)
        check("chat: a typing moves the signature though the state stays queued", typed != before)
        con.execute("UPDATE requests SET landed_at = 6 WHERE id = ?", (rid,))
        con.commit()
        check("chat: and so does the line landing in its transcript", cp.signature(con, "reviewer", True) != typed)
    finally:
        con.close()


def test_a_not_sent_or_not_cancelled_reply_is_an_error_toast() -> None:
    """T186: send-now, cancel and send report through toasts, not the sub-title."""
    from handline.ui import chat_page as cp
    from handline.ui.components.jobs_aware import Toasts

    class Stub:
        is_attached = True
        toasts: list = []
        notify = lambda self, text, **kw: self.toasts.append((text, kw["severity"]))
        toast = Toasts.toast
        refresh_conversation = lambda self, force=False: None
        _restore = lambda self, body: None
        query_one = lambda self, *a: type("B", (), {"text": "x"})()

    s = Stub()
    cp.ChatPage._sent_now(s, "not sent - busy")
    cp.ChatPage._sent_now(s, "sent")
    cp.ChatPage._cancelled(s, "not cancelled - gone", "b")
    cp.ChatPage._cancelled(s, "cancelled", "b")
    check("toast: refusals are errors, successes information",
          s.toasts == [("not sent - busy", "error"), ("sent", "information"),
                       ("not cancelled - gone", "error"), ("cancelled", "information")], str(s.toasts))


def test_a_send_that_went_shows_no_toast_and_a_refused_one_does() -> None:
    """Founder #1193: the "queued as #n" toast after a send is gone."""
    from handline.ui import chat_page as cp

    class Box:
        text = ""
        move_cursor = lambda self, *a: None
        document = type("D", (), {"end": 0})()

    class Stub:
        sending, pending, is_attached = 2, [], True
        toasts: list = []
        toast = lambda self, said, ok: self.toasts.append((said, ok))
        query_one = lambda self, sel, *a: Box() if sel == "#compose" else type("S", (), {"update": lambda s, t: None})()
        _status = lambda self: ""
        refresh_conversation = lambda self, force=False: None

    rec = cp.Pending(type("W", (), {"remove": lambda s: None})(), "hi")
    s = Stub()
    cp.ChatPage.sent(s, True, "queued as #5 for x", "hi", rec)
    cp.ChatPage.sent(s, False, "not sent: nope", "hi", rec)
    check("chat: no toast for a send that went, an error toast for a refusal",
          s.toasts == [("not sent: nope", False)], str(s.toasts))


def test_the_status_line_under_the_box_leaves_out_what_the_agent_waits_on() -> None:
    from handline.ui import chat_page as cp

    class Stub:
        replying, agent, sending, _frame = None, "x", 0, 0
        SPINNER = "-"
    s = Stub()
    s.agent_state = ("IDLE", "waiting for y's reply (#1)")
    waiting = cp.ChatPage._status(s).plain
    s.agent_state = ("IDLE", "10:30")
    check("chat: the wait is not in the status line, other details still are",
          "waiting for" not in waiting and "10:30" in cp.ChatPage._status(s).plain, waiting)


def test_a_queued_message_says_why_it_has_not_reached_the_agent() -> None:
    from handline.team import ask as askmod
    _home("qwhy")
    con = db.connect()
    try:
        first = db.add_request(con, "reviewer", "backend", "one", None, None, None, None, 0, None)
        second = db.add_request(con, actions.HUMAN, "backend", "two", None, None, None, None, 0, None)
        con.commit()
        con.execute("UPDATE agent_state SET state = 'RUNNING', terminal = 'x' WHERE agent = 'backend'")
        con.commit()
        check("chat: the head of the queue is waiting on the agent",
              "mid-turn" in actions.queue_reason(con, "backend", first), actions.queue_reason(con, "backend", first))
        check("chat: the one behind it says what is ahead",
              f"behind #{first}" == actions.queue_reason(con, "backend", second))
        actions.send_now(con, second)
        check("chat: send now marks it for the daemon", [r["id"] for r in db.send_now_pending(con, "backend")] == [second])
        check("chat: a message marked send now no longer says it is behind another",
              "behind" not in actions.queue_reason(con, "backend", second), actions.queue_reason(con, "backend", second))
        check("chat: send now refuses a request that is not the founder's",
              "not sent" in actions.send_now(con, first))
        con.execute("UPDATE requests SET delivered_at = 1 WHERE id = ?", (second,))
        check("chat: and one already delivered", "not sent" in actions.send_now(con, second))
        from handline.ui import chat_page as cp
        def buttons(delivered):
            m = {"who": "you", "when": 1, "rid": 9, "part": 0, "side": "you", "text": "x", "waiting": False,
                 "replyable": False, "answered": False, "queued": True, "delivered": delivered}
            row = [c for c in cp.ChatMessage(m)._pending_children if type(c).__name__ == "Horizontal"][0]
            return [b.label.plain if hasattr(b.label, "plain") else str(b.label)
                    for b in row._pending_children if type(b).__name__ == "Button"]
        check("chat: send now and cancel are gone once delivered",
              len(buttons(False)) == 2 and buttons(True) == [], f"{buttons(False)} {buttons(True)}")
        m = {"who": "you", "when": 1, "rid": 9, "part": 0, "side": "you", "text": "x", "waiting": False,
             "replyable": False, "answered": False, "queued": True, "delivered": False}
        idle, busy = cp.ChatMessage(m, working=False), cp.ChatMessage(m, working=True)
        check("chat (T133): send now is offered only while the agent is mid-turn",
              idle._send_now.display is False and busy._send_now.display is True,
              (idle._send_now.display, busy._send_now.display))
        idle.set_working(True)
        check("chat (T133): and appears when the agent starts a turn", idle._send_now.display is True)
    finally:
        con.close()


def test_a_blocked_heads_queue_reason_names_the_block_not_a_reversed_behind() -> None:
    """T337 round 4, auditor #2028's sweep: queue_reason compared every
    request against db.head_of_queue directly - once the true head was
    blocked, it would read 'behind #<effective head>', backward from
    queue_order (the true head sits first), and the real reason (blocked)
    was never said."""
    from handline.team import ask as askmod
    _home("qblocked")
    con = db.connect()
    try:
        ok, msg, tid = actions.task_new("product", "backend", "the blocked head", "the blocked head")
        check("setup: backend holds the task", ok, msg)
        head = db.task_requests(con, tid)[0]["id"]
        ok, msg, nxt = askmod.ask("backend", "fix it instead", requester="reviewer")
        check("setup: another request queues behind it", ok, msg)
        ok, msg, t_blocker = actions.task_new("product", "reviewer", "unrelated", "unrelated")
        check("setup: a task to block on", ok, msg)
        ok, msg = actions.add_block(con, tid, t_blocker, "product")
        check("setup: the task is now blocked", ok, msg)

        check("blocked head: names the block, not 'behind' the one that jumped it",
              actions.queue_reason(con, "backend", head) == "its own task is blocked")
        check("effective head: its own ordinary reason, not 'blocked' or 'behind'",
              "behind" not in actions.queue_reason(con, "backend", nxt)
              and "blocked" not in actions.queue_reason(con, "backend", nxt),
              actions.queue_reason(con, "backend", nxt))
    finally:
        con.close()


def test_the_context_setting_is_in_thousands_and_shows_its_share_of_the_window() -> None:
    from handline.ui import chat_page as cp
    _home("ctxset")
    check("settings: the share of a window is worked out", actions.context_share("200", "claude-opus-5") == "20% of the 1000k window"
          and actions.context_share("900", "claude-opus-5") == "held to 60% of the 1000k window"
          and actions.context_share("50", "gpt-x") == "window unknown for this model")
    cfg = cp.settings_of("backend")
    check("settings: the default reads as thousands", cfg["context_k"] == "200", cfg["context_k"])
    check("settings: 0 is refused", cp.settings_problem({**{k: cfg[k] for k in cp.EDITABLE}, "repo": "", "context_k": "0"}))
    ok, _ = cp.save_settings("backend", [("context_k", "200", "150")])
    check("settings: a save writes it as tokens", ok and agents.read("backend")["context_tokens"] == 150_000)


def test_a_message_head_tells_its_kind_and_linked_tasks() -> None:
    from handline.ui import chat_page as cp
    m = {"who": "a → b", "when": 1, "part": 0, "text": "x", "kind": "task", "tasks": [418]}
    check("head: a row shows its kind and T number",
          cp.message_parts({**m, "kind": "ask"})[0].plain.endswith("ask  T418"))
    card = cp.message_parts({**m, "task_title": "T", "task_state": "queued"})[0].plain
    check("card: a task delivery keeps its kind and T number for the card, not the head", "T418" not in card)
    line = cp.card_lines({**m, "task_title": "Fix it", "change": "queued -> doing"}).plain
    check("card: NEW TASK / CHANGED TASK lead the first line, the change is a second line with no who or time",
          line == "CHANGED TASK T418 : Fix it\nqueued -> doing", line)
    new = {**m, "task_title": "Fix it", "to": "orcapipe.core"}
    check("card: a new task is NEW TASK and says whom it was assigned to",
          cp.card_lines(new).plain == "NEW TASK T418 : Fix it\nassigned to orcapipe.core", cp.card_lines(new).plain)
    check("card: a handoff shows the text typed with it",
          cp.card_lines({**m, "task_title": "Fix it", "change": "handed to a", "note": "take it, urgent"}).plain.endswith(
              "\nhanded to a\ntake it, urgent"))
    check("card: above it, who and when only; the founder is shown as you",
          cp.card_head({**new, "who": "a → b", "when": 100}).plain.split("  ")[0] == "a → b"
          and cp.card_head({**new, "who": actions.HUMAN}).plain.startswith("you  "))
    check("head: a say carries no kind label, another kind still does",
          "say" not in cp.message_parts({**m, "kind": "say", "tasks": []})[0].plain
          and "ask" in cp.message_parts({**m, "kind": "ask"})[0].plain)
    check("card: a task note stays a row labelled task note T406",
          cp.message_parts({**m, "kind": "task_note", "tasks": [406]})[0].plain.endswith("task note  T406"))
    check("head: a reply part shows neither", "task" not in cp.message_parts({**m, "part": 1})[0].plain)


def test_what_an_agent_wrote_is_drawn_inert_on_every_part_of_the_page() -> None:
    from handline.ui import chat_page as cp
    from handline.infra import textsafe
    evil = "x\x9b31m y\x1b]8;;http://evil.example\x07link\x1b]8;;\x07 [red]not markup[/red] \x00\x1b[2J\ttab"
    m = {"who": evil, "when": 1, "rid": 1, "part": 0, "side": "agent", "text": evil, "waiting": False,
         "replyable": True, "answered": True, "queued": False, "delivered": True, "cancelled": True}
    head, body, note = cp.message_parts(m)
    d = {"id": 1, "title": evil, "state": "doing", "owner": evil, "opened_by": evil, "created_at": 1,
         "parent": (9, evil), "done": (evil, 1), "discarded": (evil, evil, 1), "merged_into": None, "asker": evil,
         "children": [(2, evil, "waiting")],
         "timeline": [{"ts": 1, "who": evil, "kind": "memo", "text": evil, "memo": None, "edited_at": 2}]}
    page = cp.task_page(d)
    texts = [head, body, *page["kids"], *page["timeline"], page["title"], page["meta"],
             cp.agent_badge("FATAL", evil)]
    suggestions = (cp.suggest("/task ", [(1, "owns", "waiting", evil, evil, 1)], [])
                   + cp.suggest("#", [(1, "owns", "waiting", evil, evil, 1)], [(2, evil, evil)]))
    leaks = [ln for t in texts for ln in t.plain.split("\n") if textsafe.has_unsafe(ln)]
    check("inert: nothing drawn carries a control character or a link", not leaks, str(leaks[:1]))
    check("inert: text that looks like markup is shown as typed, not interpreted",
          all("[red]not markup[/red]" in t.plain for t in (body, page["title"])), body.plain)
    leaked = [x.label for x in suggestions if textsafe.has_unsafe("".join(x.label))]
    check("inert: the menu labels are safe too", not leaked, str(leaked[:1]))
    check("inert: a cancelled message says why",
          note is not None and "told to stop" in note.plain)


# ---------------------------------------------------------------- ui components

def _plain(s: str) -> str:
    return tui._ANSI.sub("", s)


def test_gauge_elapsed_fraction_bar_and_usage_cells() -> None:
    from types import SimpleNamespace
    from handline.usage import status
    from handline.ui.components import gauge
    ef = gauge.elapsed_fraction
    check("gauge: a reset that is unknown or already passed has no fraction",
          ef(None, 3600, now=1000) is None and ef(0, 3600, now=1000) is None
          and ef(900, 3600, now=1000) is None and ef(1000, 3600, now=1000) is None)
    check("gauge: partway through the window is the share of it already gone",
          abs(ef(1000 + 900, 3600, now=1000) - 0.75) < 1e-9
          and ef(1000 + 3600, 3600, now=1000) == 0.0)
    check("gauge: a reset further off than the window never goes below 0",
          ef(1000 + 7200, 3600, now=1000) == 0.0)

    check("gauge: no figure is a row of dots",
          _plain(gauge.bar(None, 6)) == "·" * 6)
    check("gauge: the fill is round(pct * width)", _plain(gauge.bar(50, 10)).count("█") == 5)
    check("gauge: a figure past 100 or below 0 stays inside the bar",
          _plain(gauge.bar(250, 4)) == "████" and _plain(gauge.bar(-5, 4)) == "░░░░")
    for pct, colour, name in ((59, tui.GRN, "green"), (60, tui.YEL, "yellow"),
                              (84, tui.YEL, "yellow"), (85, tui.RED, "red")):
        check(f"gauge: {pct}% is drawn {name}", gauge.bar(pct, 10).startswith(colour), repr(gauge.bar(pct, 10)))
    last = gauge.bar(90, 10, last_seen=True)
    check("gauge: a last-seen figure keeps its fill but has no colour",
          last.startswith(tui.DIM) and not any(c in last for c in (tui.RED, tui.YEL, tui.GRN))
          and _plain(last).count("█") == 9)

    def at(elapsed, width=10, pct=50):
        return _plain(gauge.bar(pct, width, elapsed=elapsed)).index("│")
    check("gauge: the pace mark sits at its share of the width",
          at(0.0) == 0 and at(0.5) == 5 and at(0.95) == 9)
    check("gauge: the mark takes the place of a cell, it does not add one",
          len(_plain(gauge.bar(50, 10, elapsed=0.5))) == 10)
    check("gauge: past the end and on a one-cell bar the mark clamps inside the bar",
          at(1.0) == 9 and at(2.0) == 9 and _plain(gauge.bar(50, 1, elapsed=0.9)) == "│")
    check("gauge: no elapsed, no mark", "│" not in gauge.bar(50, 10))

    now = time.time()
    metered = SimpleNamespace(unmetered=False,
                              short=status.Window(300, reset=int(now + 9000)),
                              long=status.Window(10080, reset=int(now + 86400)),
                              long_trusted=40, long_source="live",
                              shown_short=lambda: (70, status.mark("live")))
    five, seven = gauge.usage_cells(metered, True, now=now)
    check("gauge: a metered account has a 5h and a 7d cell, each with its bar and figure",
          "70%" in _plain(five) and "40%" in _plain(seven) and "│" in _plain(five) and "│" in _plain(seven),
          (_plain(five), _plain(seven)))
    bare_five, _ = gauge.usage_cells(metered, False, now=now)
    check("gauge: without show_pct only the bar is drawn", "%" not in _plain(bare_five))
    check("gauge: an unmetered account has no meter to draw",
          gauge.usage_cells(SimpleNamespace(unmetered=True)) is None)


def test_agent_rows_short_model_cache_and_founder_block() -> None:
    from handline.ui.components import agent_rows as ar
    check("agent_rows: the model loses its provider prefix",
          ar.kind_short({"vendor": "zai", "model": "zai-coding-plan/glm-5.3-flash", "effort": "high"})
          == "glm-5.3-flash high")
    check("agent_rows: nor does it carry the vendor, and the effort is its short form",
          ar.kind_short({"vendor": "claude", "model": "opus", "effort": "medium"}) == "opus mid"
          and "claude" not in ar.kind_short({"vendor": "claude", "model": "opus", "effort": "medium"}))
    check("agent_rows: a model with no slash is shown as it is",
          ar.kind_short({"model": "sonnet"}).startswith("sonnet "))

    home = _home("agentrows")
    con = db.connect()
    try:
        check("agent_rows: no activity on record is no cache figure", ar.cache_left(con, "nobody", now=10_000) is None)
        db.add_event(con, "nobody", "Stop")
        last = db.last_activity(con, "nobody")
        check("agent_rows: the cache left is what remains of PROMPT_CACHE_S since the last activity",
              ar.cache_left(con, "nobody", now=last + 600) == dmn_states.PROMPT_CACHE_S - 600)
        check("agent_rows: it goes negative once the cache has lapsed",
              ar.cache_left(con, "nobody", now=last + dmn_states.PROMPT_CACHE_S + 5) == -5)

        cell = lambda left: _plain(ar.cache_cell(left))
        check("agent_rows: the cache cell is minutes, then m:ss in the last ten, then a dash",
              cell(3480) == "58m" and cell(600) == "10m" and cell(599) == "9:59"
              and cell(65) == "1:05" and cell(0) == "-" and cell(-30) == "-" and cell(None) == "-")
        check("agent_rows: an agent that ignores cache expiry shows that, not a countdown",
              _plain(ar.cache_cell(3480, True)) == "ignored" and _plain(ar.cache_cell(-30, True)) == "ignored")
        check("agent_rows: the last ten minutes are yellow, the rest plain",
              tui.YEL in ar.cache_cell(599) and tui.YEL not in ar.cache_cell(3480))

        now = 5_000
        db.block_open(con, "backend", "modal", "a dialog", "w", now=now, show_at=now + 100)
        check("agent_rows: a block not yet old enough to be said is not the founder's yet",
              ar.founder_block(con, "backend", now=now + 99) is None)
        check("agent_rows: at show_at it is",
              ar.founder_block(con, "backend", now=now + 100) is not None)
        check("agent_rows: an agent with no block has none", ar.founder_block(con, "nobody", now=now + 100) is None)
    finally:
        con.close()


def test_agent_rows_collect_carries_the_agents_own_model_and_effort() -> None:
    from handline.ui.components import agent_rows as ar
    _home("collectcfg")
    agents.register("opusy", model="claude-opus-5-5", effort="high")
    con = db.connect()
    try:
        row = tui.AgentRow("opusy", "", "opusy", False, None, "IDLE", "", None)
        extra = ar.collect(con, [row])["opusy"]
        check("agent_rows: collect shows the agent's own model and effort, not the defaults",
              extra[0] == ar.kind_short(agents.read("opusy")) and "opus" in extra[0], str(extra))
        from handline.session import idle_close
        check("agent_rows: collect flags cache expiry as ignored only for the agent whose switch is on",
              extra.cache_ignored is False)
        idle_close.set_agent_ignore_cache_expiry("opusy", True)
        check("agent_rows: ... and it does once the agent's ignore switch is on",
              ar.collect(con, [row])["opusy"].cache_ignored is True)
    finally:
        con.close()


def test_fold_urgency_ranks_what_a_fold_hides() -> None:
    from handline.team import ask as askmod
    from handline.ui.components import agent_rows as ar
    _home("foldurg")
    for n in ("w1", "w2", "w3", "w4"):
        agents.register(n)
    con = db.connect()
    try:
        check("fold_urgency: nothing to say is None", ar.fold_urgency(con, ["w1", "w2"], {}) is None)
        db.set_agent_state(con, "w1", "RUNNING")
        check("fold_urgency: work going on is a green ▶",
              ar.fold_urgency(con, ["w1", "w2"], {}) == ("▶", 1, tui.STATE_COLOR["RUNNING"]))
        db.set_agent_state(con, "w2", "RUNNING")
        check("fold_urgency: it counts the agents that share it",
              ar.fold_urgency(con, ["w1", "w2"], {})[1] == 2)
        check("fold_urgency: a stuck delivery is a yellow !, above work",
              ar.fold_urgency(con, ["w1", "w2"], {"w2": {}}) == ("!", 1, tui.YEL))
        db.set_agent_state(con, "w3", dmn_states.STALLED, detail="dead")
        check("fold_urgency: an error is a red !, above a stuck delivery",
              ar.fold_urgency(con, ["w1", "w2", "w3"], {"w2": {}}) == ("!", 2, tui.RED))
        askmod.ask("human", "a question", requester="w4")
        check("fold_urgency: an open ask to the founder is the ✉, above an error",
              ar.fold_urgency(con, ["w1", "w3", "w4"], {})[0] == "✉")
        check("fold_urgency: a red among yellows of the same rank makes the whole one red",
              ar.fold_urgency(con, ["w2", "w3"], {"w2": {}}) == ("!", 2, tui.RED))
    finally:
        con.close()


def test_agent_rows_collect_says_why_a_row_needs_the_founder() -> None:
    from handline.team import ask as askmod
    from handline.ui.components import agent_rows as ar
    _home("collect")
    agents.register("reviewer")
    askmod.ask("human", "please look", requester="reviewer")
    con = db.connect()
    try:
        rows, _notes = tui.agents_data(con)
        extra = ar.collect(con, rows)
        check("collect: one Extra per row with cells, by name", set(extra) == {r.name for r in rows}, extra)
        x = extra["reviewer"]
        check("collect: an open ask to the founder is counted on its row",
              x.asks == 1 and extra["backend"].asks == 0, x)
        check("collect: a row with nothing to say has no block, no stuck reason, no urgency",
              extra["backend"].block == "" and extra["backend"].urgency is None
              , extra["backend"])
    finally:
        con.close()


def test_agent_rows_collect_keeps_a_stuck_reason_whole() -> None:
    from handline.ui.components import agent_rows as ar
    _home("collectlong")
    agents.register("w9")
    long = "the daemon gave up after the third failed launch and will not try again " * 2
    con = db.connect()
    try:
        db.set_agent_state(con, "w9", dmn_states.STALLED, detail=long.strip())
        rows, _notes = tui.agents_data(con)
        check("agent_rows: a stuck reason is not cut short; the column ellipsizes it to its width",
              ar.collect(con, rows)["w9"].stuck == long.strip(), ar.collect(con, rows)["w9"].stuck)
    finally:
        con.close()


def test_open_proposals_lists_only_what_is_the_founders_move() -> None:
    from handline.team import ask as askmod, decisions
    from handline.ui.components import proposals as pr
    home = _home("openprops")
    agents.register("hr")
    con = db.connect()
    try:
        pids = {}
        for name in ("queued", "stuck", "approved", "rejected"):
            ok, said = hr.propose("add", name, repo="/repo", requester="hr", why=f"why {name}")
            check(f"proposals: {name} was raised", ok, said)
            pids[name] = said.split()[1]
        # An approve whose change did not go through stays approved, with its error.
        decisions.record("hr", pids["stuck"], 0, "approved", hr.subject_of(hr.load_proposal(pids["stuck"])))
        hr.save({**hr.load_proposal(pids["stuck"]), "state": "approved", "error": "orca is not answering"})
        t.approve_hr(pids["approved"])
        hr.decide(pids["rejected"], "rejected", askmod.HUMAN, "not now")
        got = {p["agent"]: p for p in pr.open_proposals(con)}
        check("proposals: one waiting on the founder is 'decide'",
              got.get("queued", {}).get("status") == pr.DECIDE, got)
        check("proposals: one approved but not applied says so, and keeps its error",
              got.get("stuck", {}).get("status") == pr.APPROVED
              and got["stuck"]["error"] == "orca is not answering", got)
        check("proposals: an applied or a rejected one is left out",
              "approved" not in got and "rejected" not in got, sorted(got))
        check("proposals: each carries its id", got["queued"]["id"] == pids["queued"])
        check("proposals: they are oldest first", [p["asked"] for p in pr.open_proposals(con)]
              == sorted(p["asked"] for p in pr.open_proposals(con)))
        ids = []
        for name in ("tie1", "tie2", "tie3", "tie4"):
            ok, said = hr.propose("add", name, repo="/repo", requester="hr", why="w")
            pid = said.split()[1]
            hr.save({**hr.load_proposal(pid), "created": 1000})
            ids.append(pid)
        tied = [p["id"] for p in pr.open_proposals(con) if p["id"] in ids]
        check("proposals: proposals raised in the same second come in a fixed order by id",
              tied == sorted(ids), tied)
        check("proposals: the age is the biggest whole unit",
              timefmt.ago_short(0, now=59) == "0m" and timefmt.ago_short(0, now=3600) == "1h" and timefmt.ago_short(0, now=86400 * 2) == "2d")
    finally:
        con.close()


class _FakeRules:
    """Stands in for `handline.team.dialog_rules`, whose store is the founder-only protected dir."""

    def __init__(self) -> None:
        self.rules: list[dict] = []

    def list_rules(self):
        return list(self.rules)

    def add(self, vendor, title, label):
        self.rules.append({"vendor": vendor, "title": title, "label": label})
        return True, "saved"

    def remove(self, vendor, title, label):
        self.rules = [r for r in self.rules if (r["vendor"], r["title"], r["label"]) != (vendor, title, label)]
        return True, "removed"


def _block(con, agent: str, kind: str, detail: dict, title: str = "t", wait_id=None, answerable=True) -> int:
    """A prompt_blocks row as the daemon's screen watch writes it."""
    return db.prompt_open(con, agent, kind, f"fp{time.time()}", title, [], detail, wait_id, answerable, int(time.time()))


def test_the_permission_modal_shows_the_whole_panel_without_the_tip() -> None:
    """T411: a multi-line command never matches the hook's one-line summary, so the panel's own
    lines are what the founder judges by, warning line included, minus the auto-mode tip."""
    from handline.ui.components.permission_modal import panel_text
    d = {"command": "Bash command", "lines": ("Bash command", "git commit -F - <<'EOF'", "msg", "EOF",
                                              "Tip: auto mode handles these prompts", "Contains simple_expansion")}
    text = panel_text(d)
    check("permission modal: every line of the command and the warning is shown, the tip is not",
          "msg" in text and "EOF" in text and "simple_expansion" in text and "Tip" not in text, text)
    check("permission modal: no panel lines falls back to the one-line command",
          panel_text({"command": "rm -rf build/", "lines": ()}) == "rm -rf build/")


def test_summary_line() -> None:
    from handline.ui.components import summary as sm
    text = lambda running, s: sm.summary_text(running, "12:00:00", s).plain
    check("summary: the daemon word comes first, and a quiet team says only that",
          text(True, sm.Summary(0, 0)) == "daemon running · 12:00:00")
    check("summary: a daemon that is down says so", "NOT RUNNING" in text(False, sm.Summary(0, 0)))
    check("summary: for you is a link to the screen's delegated action",
          any(not isinstance(sp.style, str) and "delegated" in str(sp.style.meta) for sp in sm.summary_text(True, "12:00:00", sm.Summary(0, 0, 2)).spans))
    check("summary: working, to decide and for you follow, in that order, each only when not zero",
          text(True, sm.Summary(1, 2, 3)) == "daemon running · 1 working · 2 to decide · 3 for you · 12:00:00"
          and "working" not in text(True, sm.Summary(0, 2, 0)))

    _home("summarize")
    con = db.connect()
    try:
        check("summary: nothing running or waiting counts zero",
              sm.summarize(con) == sm.Summary(0, 0, 0), sm.summarize(con))
        db.set_agent_state(con, "backend", "RUNNING")
        check("summary: a running agent counts", sm.summarize(con).running == 1)
        ok, _, tid = actions.task_new("product", actions.HUMAN, "for the founder", "the work and its done")
        check("summary: a task handed to the founder counts as for you, once queued",
              ok and sm.summarize(con).for_you == 1, sm.summarize(con))
    finally:
        con.close()


def test_key_token_translates_textual_keys() -> None:
    from handline.ui.keys import key_token
    ev = lambda key, ch=None: events.Key(key, ch)
    check("keys: a named key is its byte token",
          key_token(ev("up")) == "\033[A" and key_token(ev("enter")) == "\r"
          and key_token(ev("escape")) == "\033" and key_token(ev("ctrl+c")) == "\x03")
    check("keys: a printable character is itself", key_token(ev("q", "q")) == "q" and key_token(ev("question_mark", "?")) == "?")
    check("keys: a bare modifier or an unbound function key has nothing to say",
          key_token(ev("shift")) is None and key_token(ev("f5")) is None and key_token(ev("x", "\x00")) is None)


def test_the_agent_row_names_the_fold_alarm_and_why_it_is_stuck() -> None:
    from types import SimpleNamespace
    from handline.ui.components.agent_rows import Extra
    row = lambda **kw: SimpleNamespace(**{"twig": "", "shown": "boss", "under": 0, "state": "IDLE",
                                          "until": "", "fire": "", "ready": False, **kw})
    extra = lambda **kw: Extra(**{"kind_short": "m", "cache_left": None, "block": "", "stuck": "",
                                  "asks": 0, "urgency": None, **kw})
    page = HomePage.__new__(HomePage)
    name = lambda r, x, on: page._name_cell(r, x, on).plain
    err = ("!", 2, tui.RED)
    check("home: a fold hiding an error wears ⚠ and its count while the blink is on",
          "+3 ⚠2" in name(row(under=3), extra(urgency=err), True))
    check("home: and the icon is blank in the off phase, keeping the row's width",
          name(row(under=3), extra(urgency=err), False) == "boss +3 " + " " * 2
          and len(name(row(under=3), extra(urgency=err), False)) == len(name(row(under=3), extra(urgency=err), True)))
    check("home: a fold hiding only a limit does not blink",
          "⚠2" in name(row(under=3), extra(urgency=("!", 2, tui.YEL)), False))
    check("home: a fold hiding an ask or work shows its glyph and count",
          "✉1" in name(row(under=1), extra(urgency=("✉", 1, tui.YEL)), True)
          and "▶4" in name(row(under=4), extra(urgency=("▶", 4, "g")), True))
    check("home: the blink is only for an error under a fold",
          HomePage._fold_errs(row(under=3), extra(urgency=err))
          and not HomePage._fold_errs(row(under=3), extra(urgency=("!", 1, tui.YEL)))
          and not HomePage._fold_errs(row(under=0), extra(urgency=err))
          and not HomePage._fold_errs(row(under=3), extra()))
    check("home: a row is an alarm on a dialog or a block, not on plain work",
          HomePage._alarm(row(state="DIALOG"), extra()) and HomePage._alarm(row(), extra(block="b"))
          and not HomePage._alarm(row(state="RUNNING"), extra()))
    state = lambda r, x: page._state_cell(r, x, False).plain
    note = lambda r, x, wall="": page._note_cell(r, x, wall).plain
    check("home: the state column is the state word alone, whatever the row is stuck on or until",
          state(row(until="12:30"), extra(stuck="its delivery is not moving")) == state(row(), extra())
          and state(row(), extra(block="dialog open")) == state(row(), extra()))
    check("home: the state word does not carry the ready flag (founder #1738: it goes by the name)",
          state(row(ready=True), extra()) == state(row(), extra()))
    check("home: a ready row (T300, founder #1738) wears ⚑ by its name, blinking like an alarm",
          name(row(ready=True), extra(), True) == "boss ⚑"
          and name(row(ready=True), extra(), False) == "boss  "
          and len(name(row(ready=True), extra(), False)) == len(name(row(ready=True), extra(), True)))
    check("home: the last column says why it is stuck, and a block in its own words",
          "⚠ its delivery is not moving" in note(row(), extra(stuck="its delivery is not moving"))
          and note(row(), extra(block="dialog open")) == "dialog open" and note(row(), extra()) == "")
    check("home: the last column also holds what the state is until, then the rest, joined by ·",
          note(row(until="12:30"), extra(stuck="stuck")) == "12:30 · ⚠ stuck"
          and note(row(until="12:30"), extra(), "⏳ 14:00") .startswith("12:30 · "))


def test_account_settings_alias_and_default_cap() -> None:
    from handline.usage import load as loadmod
    from handline.session import triggers
    _home("settings")
    ok, _ = actions.save_account_settings("alpha", "x" * 41, "3")
    check("settings: an alias over 40 characters is refused", not ok and loadmod.label_of("alpha") == "alpha")
    ok, _ = actions.save_account_settings("alpha", "a\x1bb", "3")
    check("settings: an alias with a control character is refused", not ok and loadmod.label_of("alpha") == "alpha")

    actions.save_account_settings("alpha", "Work", "3")
    ok, _ = actions.save_account_settings("alpha", "Other", "0")
    check("settings: a bad cap leaves the new alias unwritten", not ok and loadmod.label_of("alpha") == "Work",
          loadmod.label_of("alpha"))
    ok, _ = actions.save_account_settings("alpha", "  ", "3")
    check("settings: a blank alias goes back to the account's own name",
          ok and loadmod.label_of("alpha") == "alpha" and "label_alpha" not in loadmod._config())

    cfg = loadmod._config()
    for bad in (None, 0, -1, True, "3"):
        cfg["active_cap"] = bad
        check(f"default cap: {bad!r} falls back to the built-in", loadmod.default_cap(cfg) == triggers.DEFAULT_ACTIVE_CAP)
    loadmod.set_default_cap(6)
    check("default cap: an account with no cap of its own follows it", loadmod.cap_of("beta") == 6)
    check("own cap: none for an account that follows the default", loadmod.own_cap("beta") is None)
    ok, _ = actions.save_account_settings("beta", "", "")
    check("settings: a blank cap writes no own cap", ok and loadmod.own_cap("beta") is None)
    actions.save_account_settings("beta", "", "4")
    check("settings: a typed cap is written as its own", loadmod.own_cap("beta") == 4 and loadmod.cap_of("beta") == 4)
    check("settings: usage recovery is on until switched off", loadmod.usage_recover_enabled("beta"))
    ok, said = actions.save_account_settings("beta", "", "", False)
    check("settings: the switch turns usage recovery off", ok and not loadmod.usage_recover_enabled("beta")
          and "recovery off" in said, said)
    actions.save_account_settings("beta", "", "")
    check("settings: a save that does not carry the switch leaves it", not loadmod.usage_recover_enabled("beta"))
    actions.save_account_settings("beta", "", "", True)
    check("settings: and back on", loadmod.usage_recover_enabled("beta"))


def test_dashboard_accounts_table_is_fed_by_accounts_data() -> None:
    """The ACCOUNTS panel is a DataTable fed by `tui.accounts_data` - the rows
    (`alpha`, not logged in so its tick is a cross, and not the account work
    goes next)."""
    _home("acctdata")
    con = db.connect()
    try:
        rows, notes = tui.accounts_data(con)
    finally:
        con.close()
    check("accounts_data: one row per registered account, keyed by name",
          [r.name for r in rows] == ["alpha"], rows)
    check("accounts_data: a not-logged-in account carries the cross and why, not 'next'",
          "\u2718" in rows[0].mark and "not logged in" in rows[0].next and "next" not in tui._ANSI.sub("", rows[0].next).replace("not logged in", ""), rows[0])
    check("accounts_data: every column the panel draws has a cell",
          set(rows[0].cells) == set(tui.ACCT_W) - {"name"}, sorted(rows[0].cells))

def test_dashboard_agents_table_is_fed_by_agents_data() -> None:
    """The AGENTS panel is a DataTable fed by `tui.agents_data`: one row per
    drawn agent with its account, kind, state and inbox as plain values, the
    state word and the fold count built by `state_cell`/`fold_suffix`."""
    _home("agentdata")
    con = db.connect()
    try:
        rows, notes = tui.agents_data(con)
    finally:
        con.close()
    r = rows[0]
    check("agents_data: one row per drawn agent, with its account and no error",
          [x.name for x in rows] == ["backend"] and r.acct == "alpha" and r.error is None
          and not notes, rows)
    check("agents_data: the inbox is a count and the state a word",
          r.inbox == 0 and r.state == tui.screen_state(db.connect(), db.get_agent_state(
              db.connect(), "backend"))[0], r)
    check("fold_suffix: nothing under it is nothing, a fold is its count, urgency follows",
          tui.fold_suffix(0, None) == ""
          and tui._ANSI.sub("", tui.fold_suffix(3, None)) == " +3"
          and tui._ANSI.sub("", tui.fold_suffix(3, ("\u26a0", 2, tui.RED))) == " +3 \u26a02")

def test_agents_data_shows_its_current_task_and_flags_it_ready() -> None:
    """T317 (#1806): `tasks` is every task the agent owns and has not
    finished - waiting ones plus its current one, not just 0/1 for whether it
    has a current task (T300). `ready` needs that current task queued, no
    open blocker on it, and the agent IDLE - T325 makes approving an
    unblocked task deliver it too (no second `ask` needed), so `ready` only
    still shows while an open blocker holds that delivery back, or once it
    clears without anything having advanced the task since."""
    _home("currenttask")
    # backend has no manager in DESIGN_TEAM's `eng` team (no member named
    # `eng`), so only the founder may approve its task - which needs its own
    # routing edge to deliver, the same one production's team.yaml carries.
    t.hand_edit_team(t.DESIGN_TEAM.replace(
        "edges:\n", 'edges:\n  - { from: "human", to: "*", relation: supervisor, '
                    'response: fire_and_forget }\n'))
    con = db.connect()
    try:
        rows, _ = tui.agents_data(con)
        r = next(x for x in rows if x.name == "backend")
        check("agents_data: no owned task is no count", r.tasks == 0, r)

        tid = db.new_task(con, "write the report", "backend", "product")
        ok, said = actions.task_start(con, "backend", tid)
        con.commit()
        check("task_start: makes it backend's current task",
              ok and db.current_task(con, "backend") == tid, said)
        rows, _ = tui.agents_data(con)
        r = next(x for x in rows if x.name == "backend")
        check("agents_data: tasks is 1 for a draft current task, not yet ready",
              r.tasks == 1 and not r.ready, r)

        blocker = db.new_task(con, "blocker", "backend", "product")
        db.add_block(con, tid, blocker)
        con.commit()
        ok, said = actions.set_queued(con, tid, actions.HUMAN, True)
        con.commit()
        check("set_queued: approves it, but an open blocker holds T325's "
              "delivery back", ok and "was free" not in said, said)
        rows, _ = tui.agents_data(con)
        r = next(x for x in rows if x.name == "backend")
        check("agents_data: queued and IDLE, but blocked - not ready; the blocker is an "
              "unapproved draft, so it is not in the count (T371)",
              r.state == "IDLE" and r.tasks == 1 and not r.ready, r)

        db.remove_block(con, tid, blocker)
        con.commit()
        rows, _ = tui.agents_data(con)
        r = next(x for x in rows if x.name == "backend")
        check("agents_data: ready once its current task is queued, unblocked and IDLE "
              "- the blocker cleared without anything having delivered it yet",
              r.state == "IDLE" and r.tasks == 1 and r.ready, r)

        actions.set_queued(con, blocker, actions.HUMAN, True)
        con.commit()
        rows, _ = tui.agents_data(con)
        r = next(x for x in rows if x.name == "backend")
        check("agents_data: an approved task is in the count", r.tasks == 2, r)
        db.mark_task_done(con, blocker, "done, T317", via="reason")
        con.commit()
        rows, _ = tui.agents_data(con)
        r = next(x for x in rows if x.name == "backend")
        check("agents_data: a done task drops out of the count", r.tasks == 1, r)
    finally:
        con.close()

def test_dashboard_messages_table_is_fed_by_messages_data() -> None:
    """The FOR YOU panel is a DataTable fed by `tui.messages_data`: a title that
    counts what is waiting, a row per message (sender, kind, its first line
    with nothing a terminal would obey in it)."""
    _home("msgdata")
    agents.register("reviewer")
    from handline.team import ask as askmod
    askmod.ask("human", "\n\033[2Jplease \033]8;;http://evil.example\033\\review\033]8;;\033\\ this",
               requester="reviewer")
    con = db.connect()
    try:
        title, rows, notes = tui.messages_data(con)
    finally:
        con.close()
    check("messages_data: the title counts what is waiting", "1 " in title and title.startswith("FOR YOU"), title)
    check("messages_data: one row from the sender, no note",
          [r.sender for r in rows] == ["reviewer"] and not notes, rows)
    check("messages_data: the first line is the first with something on it, inert",
          rows[0].first == "please review this", repr(rows[0].first))
    con = db.connect()
    try:
        db.add_request(con, "reviewer", "human", "no kind at all", None, None, None, None, 0, None)
        rows = tui.messages_data(con)[1]
    finally:
        con.close()
    check("messages_data: a row with no kind is labelled say", [r.kind for r in rows if r.first == "no kind at all"] == ["say"],
          str([(r.first, r.kind) for r in rows]))

def test_a_dialog_on_an_agent_terminal_is_read_and_pressed() -> None:
    import sys
    from unittest import mock
    from handline.session import term as termmod
    from handline.team import trust
    from handline.ui.components.dialog_modal import DialogModal
    home = _home("dialogmodal")
    agents.register("reviewer")
    rules = _FakeRules()
    con = db.connect()
    db.set_terminal(con, "reviewer", "term_d", "wt")
    con.commit()
    con.close()
    dialog = termmod.Dialog("Do you want to proceed?", ((1, "Yes"), (2, "Yes, and don't ask again"), (3, "No")), "Esc to cancel")
    screen = {"dialog": dialog, "folder": None}
    pressed = []

    def press(handle, number):
        pressed.append((handle, number))
        screen["dialog"] = None
        return termmod.CLEARED, dialog.title, ""
    with mock.patch.dict(sys.modules, {"handline.team.dialog_rules": rules}), \
            mock.patch.object(termmod, "dialog_on_screen", lambda h: screen["dialog"]), \
            mock.patch.object(termmod, "trust_prompt_folder", lambda h: screen["folder"]), \
            mock.patch.object(termmod, "trust_prompt_showing", lambda h: screen.get("trust_unreadable", False)), \
            mock.patch.object(termmod, "confirm_trust", lambda h: (True, "trusted")), \
            mock.patch.object(termmod, "press_option", press):
        import handline.team as teampkg
        teampkg.dialog_rules = rules
        con = db.connect()
        try:
            d = actions.dialog_of(con, "reviewer")
            check("dialog: a dialog on the terminal is read as title and numbered options",
                  d and d["kind"] == "dialog" and d["title"] == dialog.title and d["options"][1][1].startswith("Yes, and"), str(d))
            screen["dialog"] = termmod.Dialog(dialog.title, dialog.options, dialog.footer,
                                              (dialog.title, "Edit /tmp/x.py", "1. Yes", "2. Yes, and don't ask again", "3. No"))
            check("dialog: the panel's other lines ride along, not the title or the options",
                  actions.dialog_of(con, "reviewer")["body"] == ["Edit /tmp/x.py"])
            screen["dialog"] = dialog
            ok, said = dialogs_mod.press_choice(con, "reviewer", "Some other title", 1)
            check("dialog: a changed dialog is not pressed", not ok and not pressed, said)
            screen["dialog"] = dialog
            ok, said = dialogs_mod.press_choice(con, "reviewer", dialog.title, 2, always=True)
            check("dialog: the pick is pressed and 'always' saves a rule on the exact title and label",
                  ok and pressed == [("term_d", 2)] and rules.rules == [
                      {"vendor": "claude", "title": dialog.title, "label": "Yes, and don't ask again"}], str((said, rules.rules)))
            screen["folder"] = str(home)
            with mock.patch.object(termmod, "screen_text", lambda h: "old\n" + "\u2500" * 20 + "\n Accessing workspace:\n\n /x/y\n\n \u276f No, exit\n   Yes, I trust this folder\n\n Enter to confirm \u00b7 Esc to cancel"):
                check("dialog: the trust screen's own words ride along, without its two choices or key hint",
                      actions.dialog_of(con, "reviewer")["body"] == ["Accessing workspace:", "/x/y"],
                      str(actions.dialog_of(con, "reviewer")["body"]))
            ok, said = dialogs_mod.press_choice(con, "reviewer", f"Trust {home}?", 1, always=True)
            check("dialog: 'always' on the folder-trust screen trusts the folder, not a rule",
                  ok and trust.is_declared(home) and len(rules.rules) == 1, said)
            screen["folder"] = None
            screen["dialog"] = dialog
            check("dialog: a numbered dialog with no trust screen is offered", actions.dialog_of(con, "reviewer") is not None)
            screen["trust_unreadable"] = True
            check("dialog: a trust screen whose folder cannot be read offers nothing (T211)",
                  actions.dialog_of(con, "reviewer") is None, str(actions.dialog_of(con, "reviewer")))
            screen["trust_unreadable"] = False
            answers = actions.standing_answers()
            check("dialog: the standing answers list the rule", any(a["kind"] == "dialog" for a in answers), str(answers))
            ok, _ = actions.remove_answer("dialog", ("claude", dialog.title, "Yes, and don't ask again"))
            check("dialog: and it can be removed", ok and rules.rules == [])
        finally:
            con.close()
    del teampkg.dialog_rules

def test_idle_close_is_set_globally_and_per_agent_in_minutes() -> None:
    from handline.session import idle_close
    _home("idle_close_form")
    check("idle close: minutes become seconds, blank is none", (actions.parse_idle_close("10"), actions.parse_idle_close(" ")) == (600, None))
    ok, _ = actions.set_idle_close("20")
    check("idle close: the global one is saved", ok and idle_close.default_close_timeout_s() == 1200)
    check("idle close: blank is refused globally", not actions.set_idle_close("")[0] and not actions.set_idle_close("x")[0])
    check("idle close: an agent's own wins, and blank drops it",
          actions.set_idle_close("5", "backend")[0] and idle_close.close_timeout_s("backend") == 300
          and actions.set_idle_close("", "backend")[0] and idle_close.close_timeout_s("backend") == 1200)
    from handline.ui import chat_page as cp
    cfg = cp.settings_of("backend")
    form = {k: cfg[k] for k in cp.EDITABLE}
    ok, said = cp.save_settings("backend", cp.settings_changes(cfg, {**form, "idle_close": "0"}))
    check("idle close: the agent form saves it, 0 is never", ok and idle_close.close_timeout_s("backend") == idle_close.NEVER, said)
    check("idle close: a word is refused", cp.settings_problem({**form, "repo": "", "idle_close": "soon"}) is not None)
    before = (cp.settings_of("backend")["effort"], idle_close.close_timeout_s("backend"))
    ok, said = cp.save_settings("backend", [("effort", before[0], "low"), ("idle_close", "", "soon")])
    check("idle close: a bad one saves nothing, not even the rest of the form",
          not ok and (cp.settings_of("backend")["effort"], idle_close.close_timeout_s("backend")) == before, said)
    ok, said = cp.save_settings("backend", [("context_k", "200", "abc"), ("idle_close", "", "7")])
    check("idle close: when the rest fails it is not saved either",
          not ok and idle_close.close_timeout_s("backend") == before[1], said)


async def test_settings_save_at_once_and_going_back_asks_only_about_an_unsaved_value() -> None:
    from textual.widgets import Input, OptionList, Select, Switch
    from handline.ui.components.agent_settings_screen import AgentSettingsScreen
    from handline.ui.components.confirm import Confirm
    from handline.ui.components.pick_or_type import PickOrType
    from handline.session import idle_close
    from handline.usage import load as loadmod
    from handline.ui import chat_page as cp
    _home("save_at_once")
    app = bt.BridgeApp()
    async with app.run_test(size=(130, 50)) as pilot:
        await pilot.pause(0.3)
        app.push_screen(AgentSettingsScreen("backend"))
        await pilot.pause(0.5)
        scr = app.screen
        note = lambda n: str(scr.query_one(f"#n_{n}").render())
        check("save at once: there is no Save button on the agent form", not scr.query("#s_save") and not scr.query("#s_reset"))
        scr.query_one("#s_skip", Switch).value = True
        await pilot.pause(0.4)
        check("skip prompts: turning it on asks first, in red, and nothing is written yet",
              isinstance(app.screen, Confirm) and agents.read("backend").get("skip_permissions") is not True)
        await pilot.press("n")
        await pilot.pause(0.4)
        check("skip prompts: No puts the switch back", scr.query_one("#s_skip", Switch).value is False
              and agents.read("backend").get("skip_permissions") is not True)
        scr.query_one("#s_skip", Switch).value = True
        await pilot.pause(0.4)
        await pilot.press("y")
        await pilot.pause(0.5)
        check("save at once: (1) a switch saves the moment it flips, and says from the next launch",
              agents.read("backend").get("skip_permissions") is True and "next launch" in note("s_skip") and scr is app.screen, note("s_skip"))
        scr.query_one("#s_skip", Switch).value = False
        await pilot.pause(0.5)
        check("skip prompts: turning it off asks nothing", agents.read("backend").get("skip_permissions") is not True
              and not isinstance(app.screen, Confirm))
        box = scr.query_one("#s_idle_close", Input)
        box.focus()
        box.value = "15"
        await pilot.pause(0.2)
        check("save at once: (2) a typed value waits for Enter or for leaving the field",
              idle_close.close_timeout_s("backend") != 900)
        scr.query_one("#s_repo", Input).focus()
        await pilot.pause(0.5)
        check("save at once: (2) leaving the field saves it", idle_close.close_timeout_s("backend") == 900)
        ctx = scr.query_one("#s_context_k", Input)
        before = agents.read("backend").get("context_tokens")
        ctx.value = "0"
        ctx.focus()
        await pilot.press("enter")
        await pilot.pause(0.4)
        check("save at once: (2) an invalid value is not saved and the field says why",
              agents.read("backend").get("context_tokens") == before and "at least 1" in note("s_context_k"), note("s_context_k"))
        await pilot.press("escape")
        await pilot.pause(0.4)
        check("save at once: (4) going back with an unsaved invalid value asks", isinstance(app.screen, Confirm))
        await pilot.press("n")
        await pilot.pause(0.4)
        check("save at once: (4) and No stays on the form", isinstance(app.screen, AgentSettingsScreen))
        ctx.value = "300"
        await pilot.press("escape")
        await pilot.pause(0.6)
        check("save at once: (4) a value that is now right is saved and going back asks nothing",
              not isinstance(app.screen, (Confirm, AgentSettingsScreen)) and agents.read("backend")["context_tokens"] == 300000,
              str(agents.read("backend")))
        app.push_screen(AgentSettingsScreen("backend"))
        await pilot.pause(0.5)
        scr = app.screen
        model = scr.query_one("#s_model_box", PickOrType)
        check("save at once: (6) the model is one control that lists the known models",
              {"opus", "sonnet", "haiku"} <= set(model.known) and not scr.query("#s_model_pick"), str(model.known))
        scr.query_one("#s_model", Input).focus()
        await pilot.pause(0.3)
        await pilot.press("down", "down", "enter")
        await pilot.pause(0.6)
        picked = agents.read("backend")["model"]
        check("save at once: (6) picking one saves it", picked in model.known and picked != "sonnet" and scr.query_one("#s_model", Input).value == picked, picked)
        typed = scr.query_one("#s_model", Input)
        typed.focus()
        typed.value = "son"
        await pilot.pause(0.3)
        check("save at once: (6) typing narrows the list", scr.query_one(OptionList).option_count == 1
              and scr.query_one(OptionList).get_option_at_index(0).prompt == "sonnet")
        scr.query_one("#s_model", Input).value = "my-own-model"
        await pilot.press("enter")
        await pilot.pause(0.5)
        check("save at once: (6) a custom value can be typed instead", agents.read("backend")["model"] == "my-own-model")
        await pilot.press("escape")
        await pilot.pause(0.4)
        await pilot.press("escape")
        await pilot.pause(0.3)
        chat = cp.ChatPage("backend")
        app.push_screen(chat)
        await pilot.pause(0.6)
        chat.query_one("Tabs").active = "settings"
        await pilot.pause(0.6)
        c_ctx = chat.query_one("#s_context_k", Input)
        c_ctx.focus()
        c_ctx.value = "0"
        await pilot.press("enter")
        await pilot.pause(0.3)
        await pilot.press("escape")
        await pilot.pause(0.4)
        check("save at once: (4) the chat page's settings tab asks too when going back with a refused value",
              isinstance(app.screen, Confirm))
        await pilot.press("n")
        await pilot.pause(0.3)
        c_ctx.value = "300"
        await pilot.press("escape")
        await pilot.pause(0.6)
        check("save at once: (4) and asks nothing once it is right, going back to the chat",
              not isinstance(app.screen, Confirm) and chat.showing_settings is False)
        while len(app.screen_stack) > 2:
            app.pop_screen()
        await pilot.pause(0.4)
        page = app.screen
        page.query_one("#scope").active = "settings"
        await pilot.pause(0.8)
        check("save at once: the global settings have no Save button either", not page.query("#settings_save"))
        cap = page.query_one("#default_cap", Input)
        cap.value = "0"
        cap.focus()
        await pilot.press("enter")
        await pilot.pause(0.3)
        check("save at once: global: a refused cap says why", "whole number" in str(page.query_one("#n_default_cap").render()))
        cap.value = "7"
        await pilot.press("enter")
        await pilot.pause(0.3)
        check("save at once: global: a good cap is saved on Enter", loadmod.default_cap() == 7)
        cap.value = "9"
        page.query_one("#idle_close", Input).focus()
        await pilot.pause(0.4)
        check("save at once: global: leaving a field saves it", loadmod.default_cap() == 9)
        actions.add_answer(vendor="claude", title="Remove me?", label="Yes")
        page._draw_answers()
        await pilot.pause(0.3)
        mine = next(i for i, a in enumerate(page._answers) if "Remove me" in a["text"])
        page.query_one("#answers").move_cursor(row=mine)
        page.query_one("#answer_remove").press()
        await pilot.pause(0.4)
        check("save at once: global: Remove takes the answer under the cursor out at once",
              not any("Remove me" in a["text"] for a in actions.standing_answers()))
        order = [w.id for w in page.query("#settings_pane > *, #settings_pane SwitchRow Switch, #settings_pane #answers")
                 if w.id]
        heads = [str(h.title) for h in page.query("#settings_pane SectionHead")]
        check("settings: four sections by what is decided", heads == ["concurrency", "closing windows", "automation"], str(heads))
        check("settings: each switch sits under its section, auto-accept right above its answer list",
              order.index("g_ignore_cache_expiry") < order.index("g_handoff_note") < order.index("g_auto_accept")
              < order.index("answers") and "g_dev_mode" not in order, str(order))
        Path(os.environ.get("SETTINGS_SHOT", os.devnull)).write_text(app.export_screenshot())
        from handline.daemon import handoff_note
        from handline.session import autoaccept
        shown = {k: page.query_one(f"#g_{k}", Switch).value for k in actions.global_switches()}
        check("global switches: each shows the value that is saved", shown == actions.global_switches(), str(shown))
        for key in shown:
            page.query_one(f"#g_{key}", Switch).value = not shown[key]
        await pilot.pause(0.6)
        flipped = {"handoff_note": handoff_note.default_enabled(), "auto_accept": autoaccept.enabled(),
                   "ignore_cache_expiry": idle_close.default_ignore_cache_expiry()}
        check("global switches: flipping each saves it where the daemon reads it",
              flipped == {k: not v for k, v in shown.items()}, str(flipped))
        for on in (True, False):
            actions.save_global_switches({"handoff_note": on})
            app.push_screen(AgentSettingsScreen("backend"))
            await pilot.pause(0.5)
            labels = [str(label) for label, _ in app.screen.query_one("#s_handoff_note", Select)._options if label != Select.BLANK]
            check(f"agent form: the handoff note choice names the global value ({'on' if on else 'off'})",
                  any(l == f"follow the global switch ({'on' if on else 'off'})" for l in labels), str(labels))
            check("agent form: and draws it on the closed control",
                  f"follow the global switch ({'on' if on else 'off'})" in str(app.screen.query_one("#s_handoff_note").render())
                  or f"follow the global switch ({'on' if on else 'off'})" in _screen_text(app.screen))
            await pilot.press("escape")
            await pilot.pause(0.4)
        from handline.ui.pages.account import AccountPage
        app.push_screen(AccountPage("alpha"))
        await pilot.pause(0.6)
        acct = app.screen
        acct.query_one("#scope").active = "settings"
        await pilot.pause(0.4)
        check("account page: no Save button", not acct.query("#a_save"))
        alias = acct.query_one("#a_name", Input)
        alias.focus()
        alias.value = "first"
        await pilot.press("enter")
        await pilot.pause(0.5)
        check("account page: the alias is saved on Enter", loadmod.label_of("alpha") == "first")
        alias.value = "second"
        acct.query_one("#a_cap", Input).focus()
        await pilot.pause(0.5)
        check("account page: leaving a field saves it", loadmod.label_of("alpha") == "second")
        was = loadmod.usage_recover_enabled("alpha")
        acct.query_one("#a_recover", Switch).value = not was
        await pilot.pause(0.5)
        check("account page: the usage recovery switch saves when flipped", loadmod.usage_recover_enabled("alpha") == (not was))
        cap = acct.query_one("#a_cap", Input)
        cap.focus()
        cap.value = "0"
        await pilot.press("enter")
        await pilot.pause(0.4)
        check("account page: a refused cap says why, and the alias is not held back",
              "at least 1" in str(acct.query_one("#n_a_cap").render()) and loadmod.label_of("alpha") == "second")
        await pilot.press("escape")
        await pilot.pause(0.4)
        check("account page: going back with the refused cap still there asks", isinstance(app.screen, Confirm))
        await pilot.press("y")
        await pilot.pause(0.4)
        check("account page: Yes leaves", not isinstance(app.screen, (Confirm, AccountPage)))
        from handline.usage import cycle as cyclemod
        from handline import vendors as vendormod
        cdir = vendormod.config_dir("alpha")
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / ".claude.json").write_text('{"oauthAccount": {"subscriptionCreatedAt": "2026-09-21T12:00:00Z"}}')
        loadmod.write_config({**loadmod.config_for_write(), "month_cycle_alpha": True, "sub_end_alpha": "2031-04-05"})
        app.push_screen(AccountPage("alpha"))
        await pilot.pause(0.6)
        acct = app.screen
        acct.query_one("#scope").active = "settings"
        await pilot.pause(0.8)
        check("account cycle: the page shows the saved switch and end day",
              acct.query_one("#a_cycle", Switch).value is True and acct.query_one("#a_end", Input).value == "2031-04-05")
        month = str(acct.query_one("#a_month").render())
        check("account cycle: the overview says where the month stands (ends, with its day)",
              "Month" in month and "ends 2031-04-05" in month, month)
        acct.query_one("#scope").active = "overview"
        await pilot.pause(0.5)
        Path(os.environ.get("CYCLE_END_SHOT", os.devnull)).write_text(app.export_screenshot())
        acct.query_one("#scope").active = "settings"
        await pilot.pause(0.4)
        end = acct.query_one("#a_end", Input)
        end.focus()
        end.value = "2031-05-06"
        await pilot.press("enter")
        await pilot.pause(0.5)
        check("account cycle: an edit is saved to config.yaml", cyclemod.own_end("alpha") == "2031-05-06")
        end.value = "next month"
        await pilot.press("enter")
        await pilot.pause(0.4)
        check("account cycle: a bad date is refused, shown on the field, and the old one stays",
              cyclemod.own_end("alpha") == "2031-05-06" and "YYYY-MM-DD" in str(acct.query_one("#n_a_end").render()))
        end.value = ""
        await pilot.press("enter")
        await pilot.pause(0.5)
        check("account cycle: blank removes the end day from config.yaml",
              "sub_end_alpha" not in loadmod.config_for_write() and cyclemod.enabled("alpha"))
        acct.query_one("#a_cycle", Switch).value = False
        await pilot.pause(0.5)
        check("account cycle: the switch saves when flipped", not cyclemod.enabled("alpha"))
        check("account cycle: off reads as renewing in the overview, from the subscription start",
              "renews" in str(acct.query_one("#a_month").render())
              and "░" in str(acct.query_one("#a_month").render()) and "│" in str(acct.query_one("#a_month").render()),
              str(acct.query_one("#a_month").render()))
        acct.query_one("#scope").active = "overview"
        await pilot.pause(0.5)
        check("account page: a logged-out account says it is left out of the graphs and forecasts",
              "left out of the graphs and forecasts" in str(acct.query_one("#a_status").render()),
              str(acct.query_one("#a_status").render()))
        Path(os.environ.get("CYCLE_SHOT", os.devnull)).write_text(app.export_screenshot())
        acct.query_one("#scope").active = "settings"
        await pilot.pause(0.4)
        end.focus()
        end.value = "2031-07-08"
        await pilot.press("enter")
        await pilot.pause(0.6)
        check("account cycle: an end day alone turns the cycle on, as the command does",
              cyclemod.enabled("alpha") and acct.query_one("#a_cycle", Switch).value is True)
        await pilot.press("escape")
        await pilot.pause(0.4)


async def test_a_tasks_timeout_is_shown_on_the_board_and_the_task_page() -> None:
    from textual.widgets import DataTable, Tabs
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("task_timeout")
    now = int(time.time())
    con = db.connect()
    try:
        queued = db.new_task(con, "waiting", "backend", actions.HUMAN, timeout_s=7200)
        running = db.new_task(con, "running", "backend", actions.HUMAN, timeout_s=3600)
        late = db.new_task(con, "late", "backend", actions.HUMAN, timeout_s=3600)
        for tid in (running, late):
            _, _, rid = askmod.ask("reviewer", "work", requester=actions.HUMAN)
            db.link_request(con, tid, rid)
        con.execute("UPDATE tasks SET timeout_at = ? WHERE id = ?", (now + 4350, running))
        # past its deadline twice over: the daemon told the opener at each, and began the next stretch
        con.execute("UPDATE tasks SET timeout_at = ? WHERE id = ?", (now + 3600 - 5, late))
        for deadline, nxt in ((now - 3610, now - 5), (now - 5, now + 3600 - 5)):
            db.add_event(con, actions.HUMAN, "task_timeout_exceeded",
                         {"task_id": late, "timeout_s": 3600, "deadline": deadline, "next": nxt})
        con.commit()
        rows = {r["id"]: r for r in actions.task_rows(con)}
        text = {tid: actions.timeout_text(rows[tid]["timeout"]) for tid in (queued, running, late)}
        check("timeout: a queued task shows its length only", text[queued] == "2h00m", str(text))
        check("timeout: a doing task shows the time left to the deadline the daemon holds", text[running] == "1h12m left", str(text))
        check("timeout: one past its deadline shows how long it has been over, from the first notice",
              text[late] == "over by 1h00m" and rows[late]["timeout"]["told"] == 2, str(rows[late]["timeout"]))
        lines = actions.timeout_lines(rows[running]["timeout"])
        check("timeout: the task page says the length, when it began counting and when it ends",
              lines[0] == "timeout 1h00m" and "counting since" in lines[1] and "ends" in lines[1] and "1h12m left" in lines[1], str(lines))
        ok, said = actions.edit_task(con, late, actions.HUMAN, timeout_s=7200)
        con.commit()
        after = actions.task_timeout(con, db.get_task(con, late), "doing")
        check("timeout: edit --timeout is reflected: no longer over, a fresh 2h", ok and after["state"] == "left"
              and after["length"] == 7200 and 7190 <= after["left"] <= 7200, f"{said} {after}")
        con.execute("UPDATE tasks SET timeout_at = ? WHERE id = ?", (now + 3600 - 5, late))
        for deadline, nxt in ((now - 3610, now - 5), (now - 5, now + 3600 - 5)):
            db.add_event(con, actions.HUMAN, "task_timeout_exceeded",
                         {"task_id": late, "timeout_s": 3600, "deadline": deadline, "next": nxt})
        con.execute("UPDATE tasks SET timeout_s = 3600 WHERE id = ?", (late,))
        con.commit()
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(150, 30)) as pilot:
        await pilot.pause(0.5)
        app.screen.query_one("#scope", Tabs).active = "tasks"
        await pilot.pause(1.0)
        table = app.screen.query_one("#tasks_table", DataTable)
        cells = {str(table.get_row(k)[1]): table.get_row(k) for k in [r.key for r in table.ordered_rows]}
        shown = {ref: [str(c) for c in row] for ref, row in cells.items()}
        check("timeout: the board has a timeout column with each task's cell",
              "1h12m left" in shown.get(f"T{running}", []) and "2h00m" in shown.get(f"T{queued}", []), str(shown))
        over = next(c for c in cells[f"T{late}"] if str(c).startswith("over by"))
        check("timeout: over by is highlighted", "red" in str(over.style) and "bold" in str(over.style), str(over.style))
        app.push_screen(cp.TaskDetail(running))
        await pilot.pause(1.0)
        page_text = _screen_text(app.screen)
        check("timeout: the task page shows it", "timeout" in page_text and "counting since" in page_text, page_text[-400:])


def test_global_switches_read_and_write_the_keys_the_daemon_reads() -> None:
    from handline.daemon import handoff_note
    from handline.session import autoaccept, idle_close
    _home("global_switches")
    check("switches: they start where the fixture left them (the note is off there)",
          actions.global_switches() == {"handoff_note": False, "auto_accept": True,
                                        "ignore_cache_expiry": False}, str(actions.global_switches()))
    actions.save_global_switches({"handoff_note": True})
    check("switches: the note is on again", handoff_note.default_enabled())
    ok, said = actions.save_global_switches({"handoff_note": False, "auto_accept": False, "ignore_cache_expiry": True})
    check("switches: Save writes the ones the daemon reads from",
          ok and not handoff_note.default_enabled() and not autoaccept.enabled()
          and idle_close.default_ignore_cache_expiry() and "handoff note off" in said, said)
    check("switches: an agent with no switch of its own follows the global one", not handoff_note.enabled("backend"))
    check("switches: unchanged ones write nothing", actions.save_global_switches(actions.global_switches()) == (True, ""))


def test_home_settings_removal_and_the_delegated_view() -> None:
    from handline.ui.components.tasks_view import LISTED, TasksView
    _home("home_marks")
    for title in ("Run A?", "Run B?"):
        actions.add_answer(vendor="claude", title=title, label="Yes")
    a, b = actions.standing_answers()[:2]
    actions.remove_answer(a["kind"], a["key"])
    left = [x["text"] for x in actions.standing_answers()]
    check("home settings: a removal is at once and only that answer", a["text"] not in left and b["text"] in left, str(left))

    view = TasksView.__new__(TasksView)
    view.refresh_rows = lambda: None
    view.show_delegated()
    check("delegated view: owner human, queued and doing, nothing to check",
          (view.owner, view.states, view.closer) == (actions.HUMAN, {"queued", "doing"}, None),
          str((view.owner, view.states, view.closer)))
    view.show_open()
    check("delegated view: a plain visit afterwards is the default view again",
          (view.owner, view.states, view.closer) == (None, set(LISTED), None))
    check("task list: it opens on the open states, and done is not one",
          set(LISTED) == {"draft", "queued", "doing"} == set(actions.OPEN_STATES))
    from handline.ui.chat_page import TaskBody, TaskPane
    check("task list: the side view is the task page's component, adding no call of its own",
          issubclass(TaskPane, TaskBody) and not [n for n in vars(TaskPane) if n.startswith(("action_", "memo_"))])


def test_esc_on_a_block_modal_is_a_cancel_sent_to_the_daemon() -> None:
    from handline.ui import chat_page as cp
    page = cp.ChatPage.__new__(cp.ChatPage)
    page._blocks_done, page._block_pressing, sent, said = set(), True, [], []
    page.send_block = lambda bid, kind, choice: sent.append((bid, kind, choice))
    page.block_result = lambda kind, ok, text: said.append((kind, ok, text))
    page.block_picked(7, "permission", None, None)
    page.block_picked(8, "dialog", (1, False), {"number": 1, "always": False})
    page.block_asked(9, None)
    page.block_asked(10, (True, "sent"))
    check("block modals: Esc sends a cancel, a pick sends the pick, a finished question sends nothing more",
          sent == [(7, "permission", {"cancel": True}), (8, "dialog", {"number": 1, "always": False}),
                   (9, "question", {"cancel": True})] and said == [("question", True, "sent")], str((sent, said)))
    check("block modals: each is closed for good, so it is not offered again", page._blocks_done == {7, 8, 9, 10})


def test_the_home_cursor_moves_only_when_the_row_is_not_already_there() -> None:
    """T309: `_sync_cursor` runs every tick; a cursor already on the selected row is left alone,
    so the table's scroll is not dragged back each time."""
    from types import SimpleNamespace
    from handline.ui.pages.home import HomePage
    moves, classes = [], []
    table = SimpleNamespace(show_cursor=False, cursor_row=1, move_cursor=lambda row: moves.append(row))
    page = SimpleNamespace(panel=0, sel={0: "b"},
                           query_one=lambda box: SimpleNamespace(set_class=lambda on, name: classes.append(on)))
    sync = lambda panel=0, sel="b": (setattr(page, "panel", panel), setattr(page, "sel", {0: sel}),
                                     HomePage._sync_cursor(page, table, "#box", 0, ["a", "b", "c"]))
    sync()
    check("home cursor: already on the selected row, it is not moved", moves == [] and table.show_cursor)
    sync(sel="c")
    check("home cursor: on another row, it moves there once", moves == [2], str(moves))
    sync(panel=1, sel="a")
    check("home cursor: a panel the keys are not on shows no cursor and is not moved",
          moves == [2] and not table.show_cursor and classes[-1] is False, str((moves, classes)))
    sync(sel="gone")
    check("home cursor: a selection no longer listed moves nothing", moves == [2], str(moves))


async def test_changing_owners_on_the_task_board_does_not_hold_the_screen() -> None:
    """Founder #9423: assigning from the board froze it. `batch_task` is a handoff per task (~80 ms
    each), and it ran on the event loop; it runs on its own thread now, and the loop keeps turning."""
    import threading
    from handline.ui.components.tasks_view import TasksView
    _home("boardassign")
    agents.register("reviewer")
    ids = [actions.task_new("product", "backend", f"Task {i}", "the work and its done")[2] for i in range(3)]
    app = bt.BridgeApp()
    on, lags = {}, []

    def slow_batch(con, verb, tids, who, arg):
        on["thread"] = threading.get_ident()
        on["calls"] = on.get("calls", 0) + 1
        time.sleep(0.5)
        return [(t, True, "ok") for t in tids]
    real = actions.batch_task
    actions.batch_task = slow_batch
    try:
        async with app.run_test(size=(150, 40)) as pilot:
            await pilot.pause(0.5)
            view = app.screen.query_one(TasksView)
            view.display = True
            await pilot.pause(0.5)
            view.checked = set(ids)

            async def beat():
                while True:
                    t = time.monotonic()
                    await asyncio.sleep(0.01)
                    lags.append(time.monotonic() - t - 0.01)
            task = asyncio.create_task(beat())
            view.action_act("owner")
            await pilot.pause(0.3)
            for ch in "reviewer":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause(0.3)
            await pilot.press("y")
            await pilot.pause(0.2)
            view.action_act("priority")          # while the batch is writing
            await pilot.pause(1.2)
            task.cancel()
            check("task board: the owner change ran", "thread" in on, str(on))
            check("task board: ... off the event loop", on.get("thread") != threading.get_ident(), str(on))
            check("task board: ... and the screen kept turning while it ran", max(lags) < 0.3, f"{max(lags):.2f}s")
            msgs = [n.message for n in app._notifications]
            check("task board: its result is noted", any(actions.task_ref(ids[0]) in m for m in msgs), str(msgs))
            check("task board: a second act during it was refused, and started no second batch",
                  any("still changing 3 tasks" in m for m in msgs) and on["calls"] == 1, str((msgs, on)))
    finally:
        actions.batch_task = real




async def test_the_home_redraw_reads_on_a_worker_and_the_loop_only_paints() -> None:
    """Founder #9423: the home refresh ran `ps`, `orca` and the database on the event loop, 0.5 s freezes
    in the stall log. A redraw paints what a worker read; neither it nor the timer's tick reads on the loop."""
    import subprocess
    import threading
    _home("homeread")
    app = bt.BridgeApp()
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause(1.0)
        screen, loop, on_loop = app.screen, threading.get_ident(), []

        def spy(name, real):
            def wrapped(*a, **k):
                if threading.get_ident() == loop:
                    on_loop.append(name)
                return real(*a, **k)
            return wrapped
        real = (db.connect, subprocess.run, subprocess.Popen)
        db.connect, subprocess.run, subprocess.Popen = (spy("db.connect", real[0]), spy("subprocess.run", real[1]),
                                                         spy("subprocess.Popen", real[2]))
        try:
            check("home read: a first read has been painted", screen._snapshot is not None)
            screen._redraw()
            screen._redraw()
            await pilot.pause(2.6)      # and a timer tick
        finally:
            db.connect, subprocess.run, subprocess.Popen = real
        check("home read: a redraw and a timer tick read nothing on the loop", on_loop == [], str(on_loop))
        check("home read: the fresh read still arrives", screen._snapshot is not None)


async def test_the_task_board_and_a_task_page_read_on_a_worker_after_an_action() -> None:
    """Founder: the bridge froze for seconds after an assign. The board's redraw and the task page's
    reload both read the database; neither does it on the event loop."""
    import threading
    from handline.ui.chat_page import TaskDetail
    _home("boardread")
    ok, _, tid = actions.task_new("product", "backend", "One", "the work and its done")
    app = bt.BridgeApp()
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause(0.5)
        app.push_screen(TaskDetail(tid))
        await pilot.pause(1.0)
        page = app.screen
        body, loop, on_loop = page._body(), threading.get_ident(), []
        real = db.connect

        def spy(*a, **k):
            if threading.get_ident() == loop:
                on_loop.append(1)
            return real(*a, **k)
        db.connect = spy
        try:
            body.load()
            body.poll()
            body.post_message(body.Changed())
            await pilot.pause(0.3)
            await app.workers.wait_for_complete()
            await pilot.pause(0.2)
        finally:
            db.connect = real
        check("task page: a reload and a poll read nothing on the loop", on_loop == [], str(on_loop))
        check("task page: ... and the page is still drawn", body.d is not None and body.d["id"] == tid)
    app = bt.BridgeApp()
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause(0.5)
        loop, on_loop = threading.get_ident(), []
        board = app.screen.query_one("#tasks_view") if app.screen.query("#tasks_view") else None
        if board is None:
            return
        db.connect = spy
        try:
            board.display = True
            board.refresh_rows()
            board.refresh_rows()
            await pilot.pause(0.3)
            await app.workers.wait_for_complete()
        finally:
            db.connect = real
        check("task board: a refresh after an action reads nothing on the loop", on_loop == [], str(on_loop))


async def test_switching_tasks_blanks_the_page_until_the_new_one_is_read() -> None:
    """The page shows one task. After show(B) and before B is read, an action finds no task: it never
    runs on B with A's text."""
    from handline.ui import chat_page as cp
    _home("switchtask")
    ok, _, a = actions.task_new("product", "backend", "A", "brief of A and its done")
    ok, _, b = actions.task_new("product", "backend", "B", "brief of B and its done")
    app = bt.BridgeApp()
    real = cp.task_detail
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause(0.5)
        app.push_screen(cp.TaskDetail(a))
        await pilot.pause(1.0)
        body = app.screen._body()
        check("switch task: the first task is read", body.d is not None and body.d["id"] == a)
        depth = len(app.screen_stack)
        cp.task_detail = lambda con, tid: (time.sleep(1.0), real(con, tid))[1]
        try:
            body.show(b)
            check("switch task: at once the page holds no task", body.d is None and body.tid == b)
            body.action_edit_brief()
            body.action_assign_task()
            await pilot.pause(0.2)
            check("switch task: an action before the read opens no box on the old data",
                  len(app.screen_stack) == depth, str(app.screen_stack))
            for _ in range(100):
                if body.d is not None:
                    break
                await pilot.pause(0.1)
        finally:
            cp.task_detail = real
        check("switch task: the new task is drawn when read", body.d is not None and body.d["id"] == b)


async def test_assigning_from_a_tasks_page_hands_off_on_a_thread() -> None:
    """The handoff of one task is ~60 ms and more; it is made off the event loop and its answer is toasted."""
    import threading
    from handline.ui.chat_page import TaskDetail
    _home("pageassign")
    agents.register("reviewer")
    ok, _, tid = actions.task_new("product", "backend", "One", "the work and its done")
    on, toasts, gate = {}, [], threading.Event()

    def spy(con, t, to, text):
        on["thread"], on["to"], on["calls"] = threading.get_ident(), to, on.get("calls", 0) + 1
        gate.wait(30)           # the handoff ends when the test says so, not after a wait a loaded run can outlast
        return True, "handed"
    real = actions.assign_task
    actions.assign_task = spy
    app = bt.BridgeApp()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            app.push_screen(TaskDetail(tid))
            await pilot.pause(0.8)
            await pilot.press("a")
            await pilot.pause(0.5)
            for ch in "ok":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause(0.2)
            await pilot.press("a")                  # a second assign while the first is writing
            await pilot.pause(0.3)
            for ch in "ok":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause(0.3)
            check("task page assign: a second one during it is refused, with a note, and makes no second handoff",
                  on["calls"] == 1 and any("still changing 1 task" in str(n.message) for n in app._notifications),
                  str((on, [n.message for n in app._notifications])))
            gate.set()
            for _ in range(100):
                if any("handed" in str(n.message) for n in app._notifications):
                    break
                await pilot.pause(0.1)
            check("task page assign: the handoff was made", on.get("to") is not None, str(on))
            check("task page assign: ... off the event loop", on.get("thread") != threading.get_ident(), str(on))
            check("task page assign: ... and its answer is toasted",
                  any("handed" in str(n.message) for n in app._notifications), str(list(app._notifications)))
    finally:
        actions.assign_task = real


async def test_a_second_assign_answered_before_the_first_worker_starts_is_refused() -> None:
    """The guard is taken where the assign is decided: two answers with no event-loop turn between
    them start one handoff, not two."""
    from handline.ui.chat_page import TaskDetail
    _home("assignrace")
    ok, _, tid = actions.task_new("product", "backend", "One", "the work and its done")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        page = TaskDetail(tid)
        app.push_screen(page)
        await pilot.pause(0.8)
        started = []

        def hold(coro, **kw):       # the worker has not started: what a loaded loop looks like
            started.append(1)
            coro.close()
        body = page._body()
        body.run_worker = hold
        answers = []
        real_push = app.push_screen
        app.push_screen = lambda screen, cb=None, **kw: answers.append(cb)
        try:
            body.action_assign_task()
            body.action_assign_task()
        finally:
            app.push_screen = real_push
        answers[0](("backend", "one"))
        answers[1](("backend", "two"))
        await pilot.pause(0.2)
        check("task page assign: an answer before the first worker starts blocks the second",
              len(started) == 1 and any("still changing" in str(n.message) for n in app._notifications),
              str((started, answers, [n.message for n in app._notifications])))


async def test_the_schedules_are_reachable_on_the_agents_settings_page() -> None:
    """The Schedules section sits at the end of the agent's settings pane; its list hugs
    its rows (no blank stretch below the title) and scrolling the pane to the end brings Add and each
    row's edit and remove into the pane."""
    from handline.ui.components.schedules_section import SchedulesSection
    _home("schedules_reach")
    actions.schedule_add("backend", "0 9 * * *", "first one")
    app = bt.BridgeApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause(0.5)
        page = app.screen
        page.query_one("#scope").active = "settings"
        await pilot.pause(0.5)
        pane, sec = page.query_one("#settings_pane"), page.query_one(SchedulesSection)
        check("schedules reach: the section is as tall as its title, one row and Add, not a stretch",
              sec.region.height <= 8, str(sec.region))
        pane.scroll_end(animate=False)
        await pilot.pause(0.3)
        shown = lambda w: pane.region.y <= w.region.y and w.region.bottom <= pane.region.bottom  # noqa: E731
        check("schedules reach: scrolled to the end, Add, edit and remove are in the pane",
              all(shown(page.query_one(i)) for i in ("#sc_add", "#sc_ed_1", "#sc_rm_1")),
              str([(i, page.query_one(i).region) for i in ("#sc_add", "#sc_ed_1", "#sc_rm_1")]) + str(pane.region))


async def test_the_bridge_asks_once_before_restarting_on_new_code() -> None:
    from handline.daemon import devmode
    from handline.ui import chat_page as cp
    from handline.ui.components.confirm import Confirm
    _home("coderestart")
    real = (devmode._code_fingerprint, devmode._dev_import_check, devmode.dev_mode_enabled, bt.CODE_CHECK_S)
    bt.CODE_CHECK_S = 0.05
    code = {"fp": "v1", "checks": 0}      # the tree, the import check and the switch stood in for; the decision is devmode's own

    def fingerprint(root):
        code["checks"] += 1
        return code["fp"]

    async def after_checks(pilot, n: int = 3) -> None:
        """Until `n` more code checks have run and their answers reached the UI thread: what the
        negative assertions wait on, instead of a sleep that a loaded machine can overrun."""
        goal = code["checks"] + n
        for _ in range(400):
            if code["checks"] >= goal:
                break
            await pilot.pause(0.025)
        await pilot.pause(0.05)

    devmode._code_fingerprint = fingerprint
    devmode._dev_import_check = lambda log: True
    devmode.dev_mode_enabled = lambda: True
    try:
        app = bt.BridgeApp()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            check("code restart: an unchanged tree asks nothing", not isinstance(app.screen, Confirm))
            code["fp"] = "v2"
            await after_checks(pilot)
            check("code restart: new code asks, and does not restart on its own",
                  isinstance(app.screen, Confirm) and app.is_running and not app.restart_on_exit)
            code["fp"] = "v2b"
            await after_checks(pilot)
            check("code restart: a newer update while it is open adds no second prompt",
                  sum(isinstance(sc, Confirm) for sc in app.screen_stack) == 1, str(app.screen_stack))
            await pilot.press("n")
            await after_checks(pilot)
            check("code restart: n keeps running and neither version is asked about again",
                  app.is_running and not isinstance(app.screen, Confirm) and not app.restart_on_exit)
            code["fp"] = "v3"
            await after_checks(pilot)
            check("code restart: a newer update asks again", isinstance(app.screen, Confirm))
            await pilot.press("y")
            await pilot.pause(0.3)       # the app stops: no more checks run to wait on
            check("code restart: y exits with a restart owed", app.restart_on_exit and not app.is_running)

        code["fp"] = "w1"
        app = bt.BridgeApp()
        async with app.run_test(size=(100, 30)) as pilot:
            page = cp.ChatPage("reviewer")
            await app.push_screen(page)
            await pilot.pause()
            box = page.query_one("#compose", cp.ChatBox)
            box.text = "half a thought"
            await pilot.pause(0.2)
            code["fp"] = "w2"
            await after_checks(pilot)
            check("code restart: a draft in the box is not asked over, the question owed",
                  app.is_running and app.restart_owed and app.screen is page)
            box.text = ""
            page.sending = 1
            await after_checks(pilot)
            check("code restart: a send in flight is not asked over", app.screen is page)
            page.sending = 0
            other = Confirm("Sure", "?", yes="y", no="n")
            app.push_screen(other)
            await after_checks(pilot)
            check("code restart: a box open over the page is not asked over", app.screen is other)
            app.pop_screen()
            await after_checks(pilot)
            check("code restart: nothing left to lose, the owed question is asked",
                  isinstance(app.screen, Confirm) and app.screen is not other)
            await pilot.press("escape")
            await pilot.pause(0.2)
            await pilot.press("f5")
            await pilot.pause(0.2)
            check("code restart: F5 asks to restart by hand", isinstance(app.screen, Confirm))
            await pilot.press("y")
            await pilot.pause(0.3)
            check("code restart: and y restarts", app.restart_on_exit and not app.is_running)
    finally:
        devmode._code_fingerprint, devmode._dev_import_check, devmode.dev_mode_enabled, bt.CODE_CHECK_S = real


def test_a_code_check_that_began_before_not_now_does_not_ask_about_the_declined_version() -> None:
    """The worker's verdict reaches the UI thread after "Not now" was pressed: it names the version just
    declined, so it asks nothing; a newer version still asks."""
    app = bt.BridgeApp()
    asked = []
    app._restart_if_idle = lambda: asked.append(1)
    app._code_state.update(fp="v1", seen="v2b")
    app._decline_update()
    app._code_checked(True, "v2b")           # the stale result of a check begun before the decline
    check("code restart: a stale verdict for the declined version asks nothing",
          not asked and not app.restart_owed, str(asked))
    app._code_checked(True, "v3")
    check("code restart: a newer version asks again", asked == [1] and app.restart_owed, str(asked))


def test_every_box_over_the_page_has_a_close_button_and_escape_closes_it() -> None:
    """The wiring, not the looks: each ModalScreen under handline.ui is a Dialog (which mounts the
    shared CloseCorner and binds Escape to `close`) or takes the CloseCorner mixin itself; a
    Confirm's close is "no"; a settings screen has its Back button."""
    import importlib
    import inspect
    import pkgutil
    import handline.ui as ui
    from textual.screen import ModalScreen
    from handline.ui.components import agent_settings_screen
    from handline.ui.components.close_bar import CORNER_W, LABEL, CloseCorner, in_close_corner
    from handline.ui.components.confirm import Confirm
    from handline.ui.components.dialog import Dialog
    for m in pkgutil.walk_packages(ui.__path__, "handline.ui."):
        importlib.import_module(m.name)

    def modals(base):
        for c in base.__subclasses__():
            yield c
            yield from modals(c)
    found = {c for c in modals(ModalScreen) if c.__module__.startswith("handline.ui") and c is not Dialog}
    bad = []
    for c in sorted(found, key=lambda c: c.__name__):
        escape = [b for k in c.__mro__ for b in vars(k).get("BINDINGS", [])
                  if getattr(b, "key", b[0] if isinstance(b, tuple) else None) == "escape"]
        if not escape or not issubclass(c, CloseCorner) or not hasattr(c, "close") or not hasattr(c, "action_close"):
            bad.append(c.__name__)
    check("close: every modal has Escape, the shared close corner, close() and its action", not bad and len(found) >= 12, str(bad))
    got = []
    c = Confirm("q?")
    c.dismiss = got.append
    c.close()
    check("close: closing a Confirm without answering says no", got == [False], str(got))
    check("close: the settings screen has a Back button besides Escape",
          "as_back" in inspect.getsource(agent_settings_screen.AgentSettingsScreen.compose))
    off = {c.__name__ for c in found if not c.CLOSE_CORNER}
    check("close: a modal whose choice already closes (No, Cancel) leaves the corner off, the rest keep it",
          off == {"Confirm", "TaskAssign", "ScheduleAdd", "AnswerAdd", "SiteEdit"}, str(off))
    check("close: only the last cells of the top border are the corner",
          in_close_corner(10, 4, 60, 68, 4) and not in_close_corner(10, 4, 60, 57, 4)
          and not in_close_corner(10, 4, 60, 68, 5) and not in_close_corner(10, 4, 60, 70, 4))
    # drawn right-aligned: padding + label + padding + corner cell, the ✕ being the label's second cell
    x_cell = 10 + 60 - (len(LABEL) + 3) + 2
    check("close: the drawn ✕ close label lies in the click area", in_close_corner(10, 4, 60, x_cell, 4)
          and CORNER_W == len(LABEL) + 3)
    from types import SimpleNamespace as NS
    from textual.widgets import Static
    for on in (True, False):
        box = Static()
        CloseCorner.mark_close_corner(NS(CLOSE_CORNER=on, query_one=lambda sel: box))
        check(f"close: mark_close_corner {'puts' if on else 'leaves off'} the label, right-aligned in styles",
              (box.border_title == LABEL and box.styles.border_title_align == "right") == on)


def test_run_execs_the_same_entrypoint_only_when_a_restart_is_owed() -> None:
    import os as _os
    from handline.infra import service
    execs, real_exec, real_app, real_argv = [], _os.execv, bt.BridgeApp, sys.argv
    real_tty = sys.stdout.isatty
    owed = {"now": True}

    class FakeApp:
        def __init__(self):
            self.restart_on_exit = owed["now"]
        def run(self):
            pass
    _os.execv = lambda exe, argv: execs.append((exe, argv))
    bt.BridgeApp = FakeApp
    sys.argv = ["ocp", "bridge", "--x"]
    sys.stdout.isatty = lambda: True
    try:
        bt.run()
        check("run: a restart owed execs the entrypoint with the same arguments",
              execs == [(service.ocp_executable(), [service.ocp_executable(), "bridge", "--x"])], str(execs))
        execs.clear()
        owed["now"] = False
        bt.run()
        check("run: none owed, no exec", execs == [])
        from handline.ui import stall
        _home("run_race")
        stall.log_file().parent.mkdir(parents=True, exist_ok=True)
        if stall._pid_fd is not None:       # the runs above took the lock in this process
            _os.close(stall._pid_fd)
            stall._pid_fd = None
        child = subprocess.Popen([sys.executable, "-c", "import sys, time; sys.path.insert(0, sys.argv[1]); "
                                  "from handline.ui import stall; stall._claim_pid_file(__import__('os').getpid()); "
                                  "print('up', flush=True); time.sleep(60)", str(ROOT)], stdout=subprocess.PIPE, text=True)
        built, real_one = [], bt._one_bridge
        bt.BridgeApp = lambda: built.append(1) or FakeApp()
        bt._one_bridge = lambda *a, **k: True       # the other bridge came up after the prompt said none
        try:
            child.stdout.readline()
            owed["now"] = True
            execs.clear()
            import contextlib, io

            class Tty(io.StringIO):
                def isatty(self) -> bool:
                    return True
            buf = Tty()
            with contextlib.redirect_stdout(buf):
                out = (bt.run(), buf.getvalue())
            check("run: a bridge that lost the lock to another does not start the app",
                  out[0] == 1 and "took the lock" in out[1] and not built and not execs, str((out, built, execs)))
        finally:
            bt._one_bridge = real_one
            child.kill()
            child.wait()
    finally:
        _os.execv, bt.BridgeApp, sys.argv = real_exec, real_app, real_argv
        sys.stdout.isatty = real_tty


async def test_a_question_whose_press_raises_still_closes_with_the_reason() -> None:
    """T355: a press that raises (db, orca) must not leave the modal disabled with esc blocked."""
    from handline.ui.bridge_textual import BridgeApp
    from handline.ui.components.question_modal import QuestionModal
    _home("questionraises")
    found = {"agent": "reviewer", "header": "h", "question": "which?", "multi_select": False, "other_number": None,
             "other_text": None, "options": [{"label": "a"}, {"label": "b"}]}

    def press(choices, text):
        raise OSError("orca is gone")
    started, got = [], []
    app = BridgeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        modal = QuestionModal(found, press, lambda: started.append(1))
        app.push_screen(modal, got.append)
        await pilot.pause()
        await modal._submit((0,), None)
        await pilot.pause()
        check("question: a press that raises dismisses the modal with (False, the reason)",
              started == [1] and len(got) == 1 and got[0][0] is False and "orca is gone" in got[0][1], str(got))
        check("question: and the modal is off the stack", modal not in app.screen_stack)


async def test_the_task_timeline_shows_a_long_entry_whole_beside_its_icons() -> None:
    import html, re
    from textual.app import App
    from handline.ui import chat_page as cp
    _home("timeline_ui")
    from handline.ui.components.task_assign import TaskAssign
    ok, _, tid = actions.task_new("product", "backend", "Show it", "the work and its done")
    con = db.connect()
    try:
        actions.memo_add(con, tid, "backend", "a short one")
        actions.memo_add(con, tid, "backend", "top line\nsecond of three\nthird of three")
        actions.memo_add(con, tid, "backend", " ".join(f"word{i}" for i in range(1, 41)))
    finally:
        con.close()

    class Host(App):
        CSS_PATH = cp.Path(cp.__file__).with_name("chat_page.tcss")

        def compose(self):
            yield cp.TaskBody(tid)

    app = Host()
    async with app.run_test(size=(80, 45)) as pilot:
        await pilot.pause(0.5)
        shown = html.unescape(re.sub(r"<[^>]*>", "", app.export_screenshot())).replace("\xa0", " ")
        lines = shown.split("\n")
        check("timeline ui: a several-line memo shows its later lines",
              "second of three" in shown and "third of three" in shown)
        check("timeline ui: a memo longer than the row shows its last word, not an ellipsis",
              "word40" in shown and "…" not in shown)
        check("timeline ui: the icons sit on the first line of an entry, and a short one stays one line",
              any("top line" in ln and "✎" in ln for ln in lines) and any("a short one" in ln and "✎" in ln for ln in lines))
        d = TaskAssign("T1", "backend", ["analytics", "backend", "frontend", "reviewer"], ["frontend"])
        check("assign to: with the holder's whole team recent, the other teams are still labelled",
              [label for label, _ in d.options] == ["frontend  (recent)", "analytics  (other team)", "reviewer  (other team)"],
              str(d.options))


async def test_a_long_timeline_scrolls_to_its_last_entry_and_shows_every_line_of_each() -> None:
    """T908: the page's pane grows with the timeline, so the last entry can be scrolled to and
    no entry is cut to the room left in a screen."""
    import html, re
    from textual.app import App
    from textual.containers import VerticalScroll
    from handline.ui import chat_page as cp
    _home("timeline_scroll")
    ok, _, tid = actions.task_new("product", "backend", "Show it", "the work and its done")
    con = db.connect()
    try:
        for i in range(30):
            actions.memo_add(con, tid, "backend", f"entry {i} first\nentry {i} second\nentry {i} third")
        actions.memo_add(con, tid, "backend", "the LAST entry\nand its LAST line")
    finally:
        con.close()

    class Host(App):
        CSS_PATH = cp.Path(cp.__file__).with_name("chat_page.tcss")

        def compose(self):
            yield cp.TaskBody(tid)

    app = Host()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause(0.5)
        pane = app.query_one("#detail_pane", VerticalScroll)
        rows = list(app.query(".tl_row"))
        check("timeline scroll: no row is cut below its lines", all(r.size.height >= len(r.query_one(".tl_text").content.plain.split("\n")) for r in rows),
              str([r.size.height for r in rows]))
        pane.scroll_end(animate=False)
        await pilot.pause()
        shown = html.unescape(re.sub(r"<[^>]*>", "", app.export_screenshot())).replace("\xa0", " ")
        check("timeline scroll: scrolled to the end, the last entry's last line is on screen", "and its LAST line" in shown,
              str((pane.scroll_y, pane.max_scroll_y, pane.virtual_size, pane.size)))


async def test_the_memo_box_asks_whoever_is_picked_and_the_chat_does_not_echo_it() -> None:
    """The memo box has a recipient list - no one (a memo) or an agent (a question on the task,
    a say tied to it, the holder unchanged). Who the founder last chatted with comes first, then the
    holder's team; the question is not drawn in the chat, its answer is."""
    from textual.app import App
    from textual.widgets import Select
    from handline.daemon import sends
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("ask_holder")
    for a in ("backend", "archivist", "zed"):
        agents.register(a)
    ok, _, tid = actions.task_new("product", "backend", "Show it", "the work and its done")
    con = db.connect()
    try:
        askmod.say("zed", "hello", requester=actions.HUMAN)
        check("ask: who the founder last chatted with, newest first", actions.recent_chatted(con)[:1] == ["zed"],
              str(actions.recent_chatted(con)))
        ok, said = actions.ask_on_task(con, tid, "archivist", "  ")
        check("ask: an empty question is refused", not ok, said)
        before = db.get_task(con, tid)["owner"]
        ok, said = actions.ask_on_task(con, tid, "archivist", "why did you do it this way?")
        check("ask: the question is sent", ok, said)
        asked = [r for r in db.task_requests(con, tid) if r["target"] == "archivist" and r["kind"] == "say"]
        check("ask: it reached the agent, from the founder, with the task named, tied to the task",
              len(asked) == 1 and asked[0]["requester"] == actions.HUMAN and f"T{tid}" in asked[0]["body"]
              and "why did you do it this way?" in asked[0]["body"], str(asked))
        check("ask: the holder does not change", db.get_task(con, tid)["owner"] == before)
        ok, said = sends.say_verb(con, lambda *_: None, target=actions.HUMAN, body="because of X",
                                  parent=asked[0]["id"], requester="archivist", speaker="archivist")
        check("ask: the agent answers with no --task needed", ok, said)
        timeline = actions.task_timeline(con, tid)
        check("ask: the question and its answer are both in the task's timeline",
              any(e["kind"] == "request" and "archivist → human" in e["who"] for e in timeline)
              and any(e["kind"] == "request" and "human → archivist" in e["who"] for e in timeline), str(timeline))
        rows, _ = cp.requests_page(con, "archivist", False)
        shown = [(m["who"], m["text"]) for m in cp.messages_of(rows, "archivist")]
        check("ask: the chat shows the answer, not the founder's question",
              shown == [("archivist", "because of X")], str(shown))
        ok, _, plain = askmod.say("archivist", "an ordinary message", requester=actions.HUMAN)
        db.link_request(con, tid, plain)
        shown = [m["text"] for m in cp.messages_of(cp.requests_page(con, "archivist", False)[0], "archivist")]
        check("ask: a founder message linked to the task another way is still drawn", "an ordinary message" in shown,
              str(shown))
        first = actions.question_targets("backend", ["archivist", "backend", "zed"], ["zed"])
        check("ask: recent first, then the rest", first[0] == ["zed"] and "zed" not in first[1] + first[2], str(first))
    finally:
        con.close()

    class Host(App):
        CSS_PATH = cp.Path(cp.__file__).with_name("chat_page.tcss")

        def compose(self):
            yield cp.TaskBody(tid, keys=True)

    app = Host()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        check("ask: the Ask button is gone", not app.query("#ask"))
        to = app.query_one("#t_memo_to", Select)
        check("ask: the box offers no one, and the holder is picked", to.value == "backend", str(to.value))
        to.value = ""
        app.query_one("#t_memo_box").value = "just a note"
        app.query_one(cp.TaskBody).action_memo_add()
        await pilot.pause()
        con = db.connect()
        try:
            check("ask: no one picked is a plain memo", any(e["kind"] == "memo" and "just a note" in e["text"]
                                                             for e in actions.task_timeline(con, tid)))
        finally:
            con.close()


async def test_the_chat_strip_says_the_agents_model_account_context_and_cache_time_left() -> None:
    """The line under the chat header, read from the agent's own snapshot; it stays in the all-chat tab too."""
    from handline.ui import chat_page as cp
    from handline.ui.components.agent_strip import AgentStrip
    _home("strip")
    t.write_agent_status("backend", "alpha", ctx_pct=61, ctx_used=122000, model_id="opus-5-5", session_id="s1")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.5)
        chat = cp.ChatPage("backend")
        app.push_screen(chat)
        await pilot.pause(0.8)
        strip = chat.query_one(AgentStrip)
        line = str(strip.render())
        check("strip: model, account, context and the cache clock; no session age, hit rate or cost",
              all(x in line for x in ("opus-5-5", "alpha", "61%", "122k/200k", "cache"))
              and not any(x in line for x in ("session", "last $")) and strip.display, line)
        chat.query_one("Tabs").active = "all"
        await pilot.pause(0.5)
        check("strip: the all-chat is this agent's page too, so the strip stays pinned there", strip.display)
        chat.query_one("Tabs").active = "mine"
        await pilot.pause(0.5)
        check("strip: and on the agent's own chat", strip.display)


def test_the_task_list_takes_only_its_own_dropdowns_for_a_sort_or_a_view() -> None:
    """A Select from the pane beside the list (the memo recipient) reaches the list's handler as it bubbles:
    only the sort dropdown may change the sort and rebuild the columns."""
    from types import SimpleNamespace
    from handline.ui.components.tasks_view import TasksView
    calls: list = []
    me = SimpleNamespace(by="id", as_tree=False, owner=None, table=SimpleNamespace(focus=lambda: calls.append("focus")),
                         refresh_rows=lambda: calls.append("refresh"),
                         _sort_by=lambda v: calls.append(("sort", v)), _set_columns=lambda: calls.append("columns"))

    def changed(select_id: str, value: str):
        calls.clear()
        TasksView.on_select_changed(me, SimpleNamespace(select=SimpleNamespace(id=select_id), value=value,
                                                        stop=lambda: None))
        return list(calls)
    check("task list: the memo recipient is no sort and rebuilds nothing", changed("t_memo_to", "backend") == [])
    check("task list: the sort dropdown sorts, redraws the heads and the rows",
          changed("f_sort", "title") == [("sort", "title"), "columns", "focus", "refresh"], str(changed("f_sort", "title")))


def test_the_sandbox_banner_reads_the_lease_and_one_click_ends_it() -> None:
    """What the banner draws and its button calls: `actions`, not the widget."""
    from handline.team import agent_settings
    _home("sandbox_banner")
    check("sandbox: on until the founder turns it off", not actions.sandbox_is_off("backend"))
    check("sandbox: the form offers it with the mode, on and auto", agent_settings.settings_of("backend")["sandbox"] == "on"
          and agent_settings.settings_of("backend")["mode"] == "auto")
    ok, said = agent_settings.set_values("backend", {"sandbox": "off", "mode": "plan"})
    check("sandbox: the form's save turns it off and sets the mode", ok and actions.sandbox_is_off("backend")
          and agent_settings.settings_of("backend")["mode"] == "plan", said)
    ok, said = actions.sandbox_back_on("backend")
    check("sandbox: the button's call puts it back at once", ok and not actions.sandbox_is_off("backend"), said)
    check("sandbox: asking again says it is already on", actions.sandbox_back_on("backend") == (True, "backend: the sandbox is already on"))
    agents.register("backend", skip_permissions=True)
    cur = agent_settings.settings_of("backend")
    shown = {"mode": agent_settings.mode_in_form(cur, "auto")}
    check("mode: with skip prompts on, the mode the page shows is no change and skip stays on",
          agent_settings.changes_for("backend", shown)[0] == [] and agents.skip_permissions_in_effect("backend", agents.read("backend")))
    check("sandbox: an unreadable agent is not shown as off", not actions.sandbox_is_off("no-such-agent"))


async def test_a_scrolled_up_chat_says_how_many_messages_came_and_jumps_to_the_newest() -> None:
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("newbar")
    agents.register("reviewer")
    for i in range(15):
        askmod.ask("reviewer", f"message number {i}", requester=actions.HUMAN)
    app = bt.BridgeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        page = cp.ChatPage("reviewer")
        await app.push_screen(page)
        await pilot.pause(0.5)
        pane = page.query_one("#transcript")
        bar = page.query_one("#newbar")

        def arrive(text: str) -> None:
            askmod.ask(actions.HUMAN, text, requester="reviewer")

        async def refresh() -> None:
            page.refresh_view()
            await pilot.pause(0.5)
        check("newbar: at the bottom nothing shows", pane.is_vertical_scroll_end and not bar.display)
        arrive("while at the bottom")
        await refresh()
        check("newbar: at the bottom the view follows and no bar shows",
              pane.is_vertical_scroll_end and not bar.display)
        pane.scroll_to(y=0, animate=False)
        await pilot.pause(0.3)
        check("newbar: scrolled up with nothing new, a plain bottom button",
              bar.display and "↓ bottom" in str(bar.render()), str(bar.render()))
        arrive("first news")
        await refresh()
        check("newbar: a message arriving while scrolled up is counted",
              bar.display and "1 new message (click) ↓" in str(bar.render()), str(bar.render()))
        arrive("second news")
        arrive("third news")
        await refresh()
        check("newbar: what arrived since is counted", "3 new messages (click) ↓" in str(bar.render()),
              str(bar.render()))
        await pilot.click("#newbar")
        await pilot.pause(0.4)
        check("newbar: a click jumps to the newest and clears it", pane.is_vertical_scroll_end and not bar.display)
        pane.scroll_to(y=0, animate=False)
        await pilot.pause(0.3)
        arrive("fourth news")
        await refresh()
        await pilot.press("end")
        await pilot.pause(0.4)
        check("newbar: End does the same", pane.is_vertical_scroll_end and not bar.display)
        pane.scroll_to(y=0, animate=False)
        await pilot.pause(0.3)
        arrive("fifth news")
        await refresh()
        pane.scroll_end(animate=False)
        await pilot.pause(0.4)
        check("newbar: scrolling to the bottom by hand clears it", not bar.display and page._unseen == 0)
        pane.scroll_to(y=0, animate=False)
        await pilot.pause(0.3)
        check("newbar: and the count starts again from none", "↓ bottom" in str(bar.render()), str(bar.render()))


async def test_older_messages_loaded_above_are_not_counted_as_new() -> None:
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("newbar-older")
    agents.register("reviewer")
    for i in range(18):
        askmod.ask("reviewer", f"message number {i}", requester=actions.HUMAN)
    app = bt.BridgeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        page = cp.ChatPage("reviewer")
        await app.push_screen(page)
        await pilot.pause(0.5)
        pane, bar = page.query_one("#transcript"), page.query_one("#newbar")
        pane.scroll_to(y=0, animate=False)
        await pilot.pause(0.3)
        page.load_older()
        await pilot.pause(0.5)
        pane.scroll_to(y=0, animate=False)
        await pilot.pause(0.3)
        askmod.ask(actions.HUMAN, "one new one", requester="reviewer")
        page.refresh_view()
        await pilot.pause(0.5)
        check("newbar: after older messages were loaded, one arrival counts as one",
              bar.display and "1 new message (click) ↓" in str(bar.render()), str(bar.render()))


async def test_answering_a_message_shows_its_whole_body() -> None:
    """A long multi-line message is drawn whole (wrapped, scrollable) over the answer box, whose
    input stays focused."""
    from textual.containers import VerticalScroll
    from textual.widgets import Static
    _home("answerbody")
    body = "\n".join([f"line {i} " + "word " * 40 for i in range(1, 31)] + ["THE LAST LINE"])
    app = bt.BridgeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        box = TextPrompt("answer #7:", context=body)
        app.push_screen(box)
        await pilot.pause()
        scroll = box.query_one("#context", VerticalScroll)
        shown = box.query_one("#context Static", Static).render().plain
        check("answer: every line of the message is in the view", shown == body, shown[-40:])
        check("answer: a long message scrolls inside the space left",
              scroll.max_scroll_y > 0 and scroll.size.height <= 15, f"{scroll.max_scroll_y} {scroll.size}")
        check("answer: the input is still visible and focused",
              app.focused is box.query_one("#box") and box.query_one("#box").region.y < 30,
              str(app.focused))
        box.finish(None)


async def test_the_answer_box_fits_a_short_terminal_and_pages_the_message() -> None:
    from textual.containers import VerticalScroll
    _home("answershort")
    body = "\n".join(f"line {i} " + "word " * 12 for i in range(1, 31))
    app = bt.BridgeApp()
    async with app.run_test(size=(80, 14)) as pilot:
        await pilot.pause()
        box = TextPrompt("answer #7:", context=body)
        app.push_screen(box)
        await pilot.pause(0.3)
        ctx = box.query_one("#context", VerticalScroll)
        dialog = box.query_one("#dialog")
        check("answer 80x14: the whole dialog is on screen, its first message line too",
              dialog.region.y >= 0 and dialog.region.bottom <= 14 and ctx.region.y >= 0,
              f"{dialog.region} {ctx.region}")
        check("answer 80x14: the input is on screen and focused",
              app.focused is box.query_one("#box") and box.query_one("#box").region.bottom <= 14)
        await pilot.press("pagedown")
        await pilot.pause(0.2)
        check("answer: PageDown in the input scrolls the message", ctx.scroll_y > 0, str(ctx.scroll_y))
        was = ctx.scroll_y
        await pilot.press("pageup")
        await pilot.pause(0.2)
        check("answer: PageUp scrolls it back", ctx.scroll_y < was, str(ctx.scroll_y))
        check("answer: the hint says so", "pgup/pgdn" in str(box.query_one("#hint").render()))
        box.finish(None)
    for size in ((80, 14), (100, 24), (80, 10)):
        app = bt.BridgeApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            box = TextPrompt("answer #7:", context=body, multi=True)
            app.push_screen(box)
            await pilot.pause(0.3)
            for i in range(8):
                await pilot.press(*f"typed {i}")
                await pilot.press("shift+enter")
            await pilot.pause(0.5)
            dialog, field, hint = (box.query_one(q) for q in ("#dialog", "#box", "#hint"))
            check(f"answer e {size[0]}x{size[1]}: with 8 typed lines the dialog top, box and hint are on screen",
                  dialog.region.y >= 0 and field.region.bottom <= size[1] and hint.region.bottom <= size[1]
                  and dialog.region.bottom <= size[1],
                  f"{dialog.region} {field.region} {hint.region}")
            check(f"answer e {size[0]}x{size[1]}: the message keeps a line to show",
                  box.query_one("#context").region.height >= 1, str(box.query_one("#context").region))
            box.finish(None)


async def test_a_and_e_on_a_home_message_open_the_answer_with_its_whole_body() -> None:
    from textual.widgets import DataTable
    from handline.team import ask as askmod
    _home("answerrow")
    agents.register("reviewer")
    body = "\n".join(f"line {i} " + "word " * 30 for i in range(1, 25)) + "\nTHE LAST LINE"
    askmod.ask("human", body, requester="reviewer")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        app.screen.sel[tui.MESSAGES] = tui._keys_of(tui.MESSAGES)[0]
        app.screen.panel = tui.MESSAGES
        app.screen._redraw()
        await pilot.pause()
        for key in ("a", "e"):
            await pilot.press(key)
            await pilot.pause(0.4)
            check(f"answer row ({key}): the box opens with the message's whole body",
                  isinstance(app.screen, TextPrompt) and app.screen.context == body,
                  type(app.screen).__name__)
            if isinstance(app.screen, TextPrompt):
                app.screen.finish(None)
            await pilot.pause(0.4)


async def test_a_session_start_is_a_quiet_system_line_and_its_handoff_opens_in_a_modal() -> None:
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("sessionmark")
    agents.register("reviewer")
    askmod.ask("reviewer", "before the restart", requester=actions.HUMAN)
    handoff = "\n".join(["# handoff", *[f"line {i} of what it knew" for i in range(60)], "THE LAST LINE"])
    con = db.connect()
    try:
        db.note_session_start(con, "reviewer", "restart", False, None, handoff, "")
        db.note_session_start(con, "reviewer", "wake", True, "sess-1", "", "")        # resumed: nothing drawn
        db.note_session_start(con, "reviewer", "wake", False, None, "", "")
        con.commit()
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        page = cp.ChatPage("reviewer")
        await app.push_screen(page)
        await pilot.pause(0.5)
        marks = list(page.query(cp.SessionMark))
        lines = [str(m.render()).split("\n") for m in marks]
        check("session mark: each new conversation is two lines, when and why then 'new session', a resume draws none",
              len(marks) == 2 and "restarted fresh" in lines[0][0] and lines[0][0][2] == ":" and lines[0][1].startswith("new session")
              and lines[1][0].endswith("after idle close") and lines[1][1] == "new session"
              and "woken" not in str(lines) and "resumed" not in str(lines), str(lines))
        check("session mark: only the one with a handoff says it can be read",
              lines[0][1] == "new session with handoff" and "handoff" not in lines[1][1], str(lines))
        check("session mark: it is not drawn as a message from anyone",
              not any(isinstance(m, cp.ChatMessage) for m in marks))
        await pilot.click(marks[1], offset=(marks[1].size.width // 2, 1))
        await pilot.pause(0.3)
        check("session mark: a start with no handoff opens nothing", app.screen is page)
        await pilot.click(marks[0], offset=(marks[0].size.width // 2, 0))
        await pilot.pause(0.3)
        check("session mark: a click off the word opens nothing", app.screen is page)
        await pilot.click(marks[0], offset=((marks[0].size.width - 24) // 2 + 20, 1))
        await pilot.pause(0.4)
        check("session mark: a click on 'handoff' opens that session's handoff in a modal",
              isinstance(app.screen, cp.HandoffModal) and app.screen.text == handoff, type(app.screen).__name__)
        body = app.screen.query_one("#handoff_body")
        check("session mark: the modal is scrollable and shows the text from its top",
              body.max_scroll_y > 0 and "line 0 of what it knew" in str(app.screen.query_one("#handoff_body Static").render()))
        await pilot.press("pagedown")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.3)
        check("session mark: esc closes the modal", app.screen is page)
        marks[0].focus()
        await pilot.press("enter")
        await pilot.pause(0.3)
        check("session mark: Enter on it opens it as well", isinstance(app.screen, cp.HandoffModal))
        await pilot.press("escape")
        await pilot.pause(0.3)
        con = db.connect()
        try:
            db.note_session_start(con, "reviewer", "restart", True, "sess-2", "", "")
            con.commit()
        finally:
            con.close()
        page.refresh_view()
        await pilot.pause(0.4)
        check("session mark: a resumed start written while the page is open draws nothing",
              len(list(page.query(cp.SessionMark))) == 2, str(len(list(page.query(cp.SessionMark)))))
        con = db.connect()
        try:
            db.note_session_start(con, "reviewer", "fresh close", False, None, "", "")
            con.commit()
        finally:
            con.close()
        page.refresh_view()
        await pilot.pause(0.4)
        check("session mark: a fresh start written while the page is open is drawn on the next refresh, with no new message",
              len(list(page.query(cp.SessionMark))) == 3, str(len(list(page.query(cp.SessionMark)))))


async def test_an_hr_request_is_a_card_the_founder_answers_in_the_chat_and_on_the_hr_page() -> None:
    from textual.app import App
    from textual.widgets import Button, DataTable, Input
    from handline.ui import chat_page as cp
    from handline.ui.components import hr_card
    _home("hr-card")
    base = {"id": "p1", "kind": "add", "agent": "scout", "requested_by": "hr", "why": "a second pair of eyes",
            "text": "vendor claude\nmodel opus", "ts": int(time.time()) - 90, "subject": "s", "error": None}
    calls: list[tuple] = []
    verdicts = [(True, "ok")]

    def stub(p, verdict, note=""):
        calls.append((p["id"], verdict, note))
        return verdicts[0]

    class Host(App):
        def compose(self):
            for state in ("proposed", "applied", "rejected"):
                yield hr_card.HrCard({**base, "id": state, "state": state}, stub)

    app = Host()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)
        cards = {c.p["state"]: c for c in app.query(hr_card.HrCard)}
        shown = lambda c: c.query(".hr_buttons").first().display
        check("hr card: a waiting request has Approve and Reject, a decided one has neither",
              shown(cards["proposed"]) and not shown(cards["applied"]) and not shown(cards["rejected"]))
        check("hr card: it says what is asked, by whom and why",
              all(w in str(cards["proposed"]._head.render()) + str(cards["proposed"]._body.render())
                  for w in ("add scout", "from hr", "waiting for you", "model opus", "a second pair of eyes")))
        check("hr card: decided ones say so", "applied" in str(cards["applied"]._head.render())
              and "rejected" in str(cards["rejected"]._head.render()))
        card = cards["proposed"]
        await pilot.click(card.query_one(".hr_reject", Button))
        await pilot.pause(0.2)
        why = card.query_one(".hr_why", Input)
        check("hr card: Reject asks why, and nothing is decided yet", card.has_class("rejecting") and not calls)
        await pilot.press("enter")
        check("hr card: an empty reason rejects nothing", not calls and card.has_class("rejecting"))
        await pilot.press("escape")
        await pilot.pause(0.2)
        check("hr card: Esc takes the question back", not card.has_class("rejecting") and not calls)
        verdicts[0] = (False, "refused")
        await pilot.click(card.query_one(".hr_approve", Button))
        await pilot.pause(0.2)
        check("hr card: a refused decision leaves the card waiting",
              calls == [("proposed", "approved", "")] and card.p["state"] == "proposed" and shown(card))
        verdicts[0] = (True, "ok")
        await pilot.click(card.query_one(".hr_reject", Button))
        await pilot.pause(0.2)
        await pilot.press(*"too wide", "enter")
        await pilot.pause(0.2)
        check("hr card: the reason is the rejection", calls[-1] == ("proposed", "rejected", "too wide")
              and card.p["state"] == "rejected" and not shown(card))

    _home("hr-card-chat")
    agents.register("backend")
    ok, said = hr.propose("settings", "backend", requester="backend", why="it reads the docs",
                          sets={"browser": "playwright", "browser_sites": "https://docs.example.org"})
    check("hr card: a settings request is raised", ok, said)
    app = bt.BridgeApp()
    async with app.run_test(size=(110, 40)) as pilot:
        page = cp.ChatPage("backend")
        await app.push_screen(page)
        await pilot.pause(0.5)
        cards = list(page.query(hr_card.HrCard))
        check("hr card: the chat of the agent that asked shows one card for it",
              len(cards) == 1 and cards[0].p["state"] == "proposed", str(len(cards)))
        await pilot.click(cards[0].query_one(".hr_approve", Button))
        await pilot.pause(0.8)
        check("hr card: Approve in the chat decides it - the settings are applied, no HR page opened",
              "docs.example.org" in str(agents.read("backend").get("mcp_config"))
              and app.screen is page, str(agents.read("backend")))
        page.refresh_view()
        await pilot.pause(0.5)
        kept = list(page.query(hr_card.HrCard))
        check("hr card: after a refresh the approved request keeps its card, drawn with its outcome",
              len(kept) == 1 and kept[0].p["state"] == "applied" and not kept[0].decidable
              and "applied" in str(kept[0]._head.render()), str([c.p["state"] for c in kept]))
        ok, said = hr.propose("settings", "backend", requester="backend", why="too eager",
                              sets={"effort": "low"})
        page.refresh_view()
        await pilot.pause(0.5)
        waiting = [c for c in page.query(hr_card.HrCard) if c.decidable]
        check("hr card: a second request is a second card, the first still there",
              len(waiting) == 1 and len(list(page.query(hr_card.HrCard))) == 2)
        waiting[0].reject()
        await pilot.pause(0.2)
        await pilot.press(*"no", "enter")
        await pilot.pause(0.8)
        page.refresh_view()
        await pilot.pause(0.5)
        states = sorted(c.p["state"] for c in page.query(hr_card.HrCard))
        check("hr card: a rejected one stays too, and nothing was applied",
              states == ["applied", "rejected"] and agents.read("backend")["effort"] != "low", str(states))

    _home("hr-card-direct")
    agents.register("backend")
    ok, said = hr.enact("settings", "backend", "by hand", sets={"effort": "high"})
    check("hr card: a change the founder made directly is no request card, in an empty chat either",
          ok and cp.hr_cards("backend", []) == [], said)

    _home("hr-card-failed")
    agents.register("backend")
    hr.propose("settings", "backend", requester="backend", why="w", sets={"effort": "high"})
    asked = hr_card.pending()[0]
    real = hr._DOERS["settings"]
    hr._DOERS["settings"] = lambda prop, con: (False, "locked")
    try:
        hr_card.decide(asked, "approved")
    finally:
        hr._DOERS["settings"] = real
    failed = hr_card.pending()
    check("hr card: an approve that could not be carried out keeps its card, approved with its error, and can be retried",
          [c["state"] for c in failed] == ["approved"] and "locked" in str(failed[0]["error"]), str(failed))
    from handline.ui.components.hr_card import body, head
    check("hr card: it says so and shows why", "apply failed" in head(failed[0]).plain
          and "locked" in body(failed[0]).plain)
    check("hr card: and the retry asks nothing new",
          hr_card.decide(failed[0], "approved")[0] and [c["state"] for c in hr_card.pending()] == ["applied"])

    _home("hr-card-page")
    agents.register("backend")
    hr.propose("settings", "backend", requester="backend", why="it reads the docs", sets={"effort": "high"})
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause(0.3)
        app.screen.query_one("#scope").active = "hr"
        await pilot.pause(0.5)
        cards = list(app.screen.query(hr_card.HrCard))
        check("hr card: the HR page shows the same card for the selected request", len(cards) == 1
              and cards[0].decidable)
        app.screen.query_one("#hr_table", DataTable).focus()
        await pilot.press("a")
        await pilot.pause(0.8)
        check("hr card: a on the HR page approves it through the card", agents.read("backend")["effort"] == "high",
              str(agents.read("backend")))
        agents.register("analytics")
        hr.propose("settings", "analytics", requester="analytics", why="w", sets={"effort": "high"})
        hr.propose("settings", "backend", requester="backend", why="w", sets={"model": "haiku"})
        app.screen.query_one("#hr_table", DataTable).focus()
        await pilot.pause(2.5)
        await pilot.press("A")
        await pilot.pause(0.4)
        check("hr card: A asks once before approving everything waiting", isinstance(app.screen, Confirm)
              and agents.read("analytics").get("effort") != "high")
        await pilot.press("y")
        await pilot.pause(1.0)
        check("hr card: confirming applies every waiting proposal in one action",
              agents.read("analytics")["effort"] == "high" and agents.read("backend")["model"] == "haiku"
              and not hr.pending(), str(hr.pending()))


async def test_every_dialog_opens_on_its_field_and_leaves_once() -> None:
    """T767: the boxes over a page share `Dialog`: focus lands on the box's own field, Escape
    closes it, a second answer is dropped, and the page under it has focus back after."""
    from handline.ui.bridge_textual import BridgeApp
    from handline.ui.components.answer_add import AnswerAdd
    from handline.ui.components.confirm import Confirm
    from handline.ui.components.schedule_add import ScheduleAdd
    from handline.ui.components.task_assign import TaskAssign
    from handline.ui.components.text_prompt import TextPrompt
    _home("dialogs")
    made = {"assign": lambda: TaskAssign("T1", "backend", ["analytics", "frontend"]),
            "prompt": lambda: TextPrompt("what?", multi=True), "line": lambda: TextPrompt("what?"),
            "answer": AnswerAdd, "schedule": ScheduleAdd, "confirm": lambda: Confirm("sure")}
    app = BridgeApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        under = app.focused
        for name, make in made.items():
            got: list = []
            box = make()
            app.push_screen(box, got.append)
            await pilot.pause()
            check(f"dialog {name}: opens with focus on its own field",
                  box.FOCUS is None or app.focused is box.query_one(box.FOCUS), str(app.focused))
            box.finish("first")
            box.finish("second")
            await pilot.pause()
            check(f"dialog {name}: a second answer is dropped and the box is off the stack",
                  got == ["first"] and box not in app.screen_stack, str(got))
            check(f"dialog {name}: what was under it has focus back", app.focused is under, str(app.focused))
            box = make()
            app.push_screen(box, got.append)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            check(f"dialog {name}: Escape closes it", box not in app.screen_stack)


async def test_assign_enter_sends_the_note_whole() -> None:
    """T767: the assign box is a `TextBox` like the other prompts: Enter sends, shift+enter is a new line."""
    from handline.ui.bridge_textual import BridgeApp
    from handline.ui.components.task_assign import TaskAssign
    _home("assign_enter")
    got: list = []
    app = BridgeApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.push_screen(TaskAssign("T1", "backend", ["analytics"]), got.append)
        await pilot.pause()
        await pilot.press(*"한글 note")
        await pilot.press("enter")
        await pilot.pause()
        check("assign box: Enter sends the typed note to the chosen agent", got == [("analytics", "한글 note")], str(got))


async def test_a_changed_timeline_entry_remounts_only_itself() -> None:
    """T767: an agent's one new memo on a 100-row timeline leaves the other rows as they were;
    an edited memo replaces its own row in place, a deleted one just goes."""
    from textual.app import App
    from handline.ui import chat_page as cp
    _home("timeline_rows")
    ok, _, tid = actions.task_new("product", "backend", "Long", "the work and its done")
    con = db.connect()
    try:
        ids = [actions.memo_add(con, tid, "backend", f"memo {i}")[2] for i in range(100)]
    finally:
        con.close()

    class Host(App):
        CSS_PATH = cp.Path(cp.__file__).with_name("chat_page.tcss")

        def compose(self):
            yield cp.TaskBody(tid)

    def change(f) -> None:
        con = db.connect()
        try:
            f(con)
        finally:
            con.close()
    app = Host()
    async with app.run_test(size=(100, 40)) as pilot:
        body = app.query_one(cp.TaskBody)
        await pilot.pause()
        before = list(body.query(".tl_row"))
        change(lambda con: actions.memo_add(con, tid, "backend", "one more"))
        body.poll()
        await pilot.pause()
        after = list(body.query(".tl_row"))
        check("timeline rows: a new memo adds one row and keeps the other rows themselves",
              len(after) == len(before) + 1 and after[:len(before)] == before, f"{len(before)} -> {len(after)}")
        at = lambda rows, mid: next(i for i, r in enumerate(rows) if r.query(f"#me_{mid}"))      # the task's own request comes first
        i = at(after, ids[40])
        change(lambda con: actions.memo_edit(con, ids[40], "backend", "memo 40 rewritten"))
        body.poll()
        await pilot.pause()
        now = list(body.query(".tl_row"))
        check("timeline rows: an edited memo replaces its own row, in its place, and no other",
              len(now) == len(after) and now[i] is not after[i] and now[:i] == after[:i] and now[i + 1:] == after[i + 1:])
        check("timeline rows: the edited row shows the new text and its icons still answer",
              "rewritten" in str(now[i].query_one(".tl_text").render()) and body.query_one(f"#me_{ids[40]}") is not None)
        j = at(now, ids[10])
        change(lambda con: actions.memo_delete(con, ids[10], "backend"))
        body.poll()
        await pilot.pause()
        gone = list(body.query(".tl_row"))
        check("timeline rows: a deleted memo's row goes and the rest stay",
              len(gone) == len(now) - 1 and gone[:j] == now[:j] and gone[j:] == now[j + 1:])


async def test_the_task_page_edits_the_task_text_and_shows_a_refusal() -> None:
    """T703: the page's edit-text button rewrites the brief its opener sent, the change is on the
    timeline, and a refusal (nothing changed, nothing written) is shown, not swallowed."""
    from textual.widgets import Button
    from handline.ui import chat_page as cp
    from handline.ui.bridge_textual import BridgeApp
    _home("editbrief")
    agents.register("reviewer")
    con = db.connect()
    try:
        ok, _, tid = actions.task_new(actions.HUMAN, "reviewer", "Fix it", "the work and its done")
        check("edit text: a task's brief is the text its opener sent", actions.task_brief(con, tid).endswith("the work and its done"))
        older = db.add_request(con, "reviewer", actions.HUMAN, "someone's older ask", None, None, "fire_and_forget", "say", 0, None)
        ok, _, linked = actions.task_new(actions.HUMAN, "reviewer", "Linked", "the opener's own brief")
        db.link_request(con, linked, older)
        con.commit()
        check("edit text: the page draws the brief the edit changes, not an older request linked later",
              cp.task_detail(con, linked)["brief"] == "the opener's own brief", str(cp.task_detail(con, linked)["brief"]))
        actions.edit_task(con, linked, actions.HUMAN, body="Linked\nrewritten")
        check("edit text: and after the edit the page shows it", cp.task_detail(con, linked)["brief"] == "rewritten",
              str(cp.task_detail(con, linked)["brief"]))
    finally:
        con.close()
    app = BridgeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.push_screen(cp.TaskDetail(tid))
        await pilot.pause()
        body = app.screen.query_one(cp.TaskBody)
        button = body.query_one("#t_brief_edit", Button)
        check("edit text: the page offers it", button.display)
        seen: list = []
        body.notify = lambda said, **kw: seen.append((said, kw.get("severity")))
        await pilot.click("#t_brief_edit")
        await pilot.pause()
        box = app.screen.query_one("#box")
        check("edit text: the prompt opens on the whole text", "the work and its done" in box.text)
        box.text = "the work, reworded"
        await pilot.press("enter")
        await pilot.pause()
        con = db.connect()
        try:
            check("edit text: the brief is rewritten", actions.task_brief(con, tid) == "the work, reworded", str(actions.task_brief(con, tid)))
            check("edit text: and the timeline says so", [e["text"] for e in actions.task_timeline(con, tid) if e["kind"] == "edit"] == ["the work, reworded"])
        finally:
            con.close()
        await pilot.click("#t_brief_edit")
        await pilot.pause()
        await pilot.press("enter")          # the same text again
        await pilot.pause()
        check("edit text: nothing changed is refused and the refusal is shown",
              seen and seen[-1][1] == "error" and "nothing to edit" in seen[-1][0], str(seen))
        await pilot.click("#t_brief_edit")
        await pilot.pause()
        app.screen.query_one("#box").text = "  "
        await pilot.press("enter")
        await pilot.pause()
        check("edit text: a blank text is refused and shown", seen[-1][1] == "error" and "needs a body" in seen[-1][0], str(seen))


async def test_the_task_page_queues_a_draft_and_drafts_a_queued_task() -> None:
    """One button, Queue on a draft and Draft on a queued task, through actions.set_queued; a done task has none."""
    from textual.widgets import Button
    from handline.ui import chat_page as cp
    from handline.ui.bridge_textual import BridgeApp
    _home("queuebtn")
    agents.register("reviewer")
    con = db.connect()
    try:
        actions.task_new(actions.HUMAN, "reviewer", "Busy", "keeps the owner occupied so a queued task waits its turn")
        tid = db.new_task(con, "Fix it", "reviewer", actions.HUMAN)
        was = actions.task_state(con, db.get_task(con, tid))
    finally:
        con.close()
    check("queue button: the task starts as a draft", was == "draft", was)
    app = BridgeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.push_screen(cp.TaskDetail(tid))
        await pilot.pause(0.5)
        body = app.screen.query_one(cp.TaskBody)
        btn = body.query_one("#queue", Button)

        def state() -> str:
            con = db.connect()
            try:
                return actions.task_state(con, db.get_task(con, tid))
            finally:
                con.close()
        first = state()
        want = "queued" if first == "draft" else "draft"
        check("queue button: the label names what a press does",
              str(btn.label).startswith("Queue" if first == "draft" else "Draft") and not btn.disabled, str(btn.label))
        await pilot.click("#queue")
        await pilot.pause(0.8)
        check("queue button: a press flips the task", state() == want, f"{first} -> {state()}")
        check("queue button: and the label flips with it", str(btn.label).startswith("Queue" if want == "draft" else "Draft"), str(btn.label))
        await pilot.press("u")
        await pilot.pause(0.8)
        check("queue button: the u key flips it back", state() == first, state())
        Path(os.environ.get("QUEUE_SHOT", os.devnull)).write_text(app.export_screenshot())
        con = db.connect()
        try:
            actions.mark_task_done(con, tid, actions.HUMAN, reason="done by hand")
        finally:
            con.close()
        body.load()
        await pilot.pause(0.5)
        check("queue button: a done task cannot be queued", btn.disabled)


async def test_an_open_task_page_shows_what_an_agent_adds_without_reopening() -> None:
    """T525: a memo or a handoff made while the page is open appears on the next poll."""
    from handline.ui import chat_page as cp
    from handline.ui.bridge_textual import BridgeApp
    _home("livetimeline")
    agents.register("reviewer")
    con = db.connect()
    try:
        ok, _, tid = actions.task_new(actions.HUMAN, "reviewer", "Fix it", "the work and its done")
    finally:
        con.close()
    app = BridgeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        cp.TaskBody.POLL_S = 0.05
        await app.push_screen(cp.TaskDetail(tid))
        await pilot.pause()
        body = app.screen.query_one(cp.TaskBody)
        con = db.connect()
        try:
            actions.memo_add(con, tid, "reviewer", "posted while it was open")
        finally:
            con.close()
        for _ in range(400):    # the page's own timer, no poll() by hand; a loaded run may take seconds to tick
            await pilot.pause(0.05)
            if body.d and body.d["timeline"]:
                break
        cp.TaskBody.POLL_S = 2
        body._poller.stop()     # what follows calls poll() by hand
        check("task page: its own timer draws a memo an agent added while it was open",
              [e["text"] for e in body.d["timeline"] if e["kind"] == "memo"] == ["posted while it was open"], str(body.d["timeline"]))
        loads = []
        real = body._draw
        body._draw = lambda d: (loads.append(1), real(d))[1]

        async def polled() -> None:         # the poll reads on a worker: wait for it to land
            body.poll()
            await pilot.pause(0.05)
            await app.workers.wait_for_complete()
            await pilot.pause(0.05)
        await polled()
        check("task page: a poll with nothing new leaves the page alone", loads == [])
        con = db.connect()
        try:
            actions.memo_add(con, tid, "reviewer", "found the cause")
        finally:
            con.close()
        await polled()
        check("task page: a memo added meanwhile is drawn on the next poll",
              [e["text"] for e in body.d["timeline"] if e["kind"] == "memo"] == ["posted while it was open", "found the cause"]
              and len(loads) == 1, str(body.d["timeline"]))
        await polled()
        check("task page: and the poll after that leaves it alone again", len(loads) == 1)


def test_the_bridge_stylesheet_parses() -> None:
    """A stray brace in chat_page.tcss stops `hl` from opening at all."""
    from textual.app import App
    from textual.css.stylesheet import Stylesheet
    from handline.ui.bridge_textual import BridgeApp
    sheet = Stylesheet(variables=App().get_css_variables())
    sheet.read(BridgeApp.CSS_PATH)
    try:
        sheet.parse()
        ok, why = True, ""
    except Exception as e:  # noqa: BLE001
        ok, why = False, str(e)[:200]
    check("bridge: chat_page.tcss parses", ok, why)


def test_the_open_task_counts_read_once_and_match_the_one_agent_count() -> None:
    _home("task_counts")
    agents.register("reviewer")
    con = db.connect()
    try:
        ok, _, t1 = actions.task_new("product", "backend", "One", "the work and its done")
        ok, _, t2 = actions.task_new("product", "backend", "Two", "the work and its done")
        ok, _, t3 = actions.task_new("product", "reviewer", "Three", "the work and its done")
        counts = actions.open_task_counts(con)
        each = {n: actions.open_task_count(con, n) for n in ("backend", "reviewer", "product")}
        check("task counts: every owner's count is the one-agent count, and an owner with none is left out",
              counts == {n: c for n, c in each.items() if c} and each["backend"] >= 1 and each["product"] == 0,
              str((counts, each)))
        seen: list[str] = []
        con.set_trace_callback(lambda q: seen.append(q) if "FROM tasks" in q else None)
        actions.open_task_counts(con)
        con.set_trace_callback(None)
        check("task counts: the open tasks are scanned once for all agents",
              len([q for q in seen if "discarded_by IS NULL" in q]) == 1, str(seen))
    finally:
        con.close()


def test_agents_data_scans_the_open_tasks_once_for_all_agents() -> None:
    _home("agents_scan")
    for n in ("reviewer", "product"):
        agents.register(n)
    con = db.connect()
    try:
        seen: list[str] = []
        con.set_trace_callback(lambda q: seen.append(q) if "FROM tasks" in q else None)
        rows, _ = tui.agents_data(con)
        con.set_trace_callback(None)
        scans = [q for q in seen if "discarded_by IS NULL" in q]
        check("agents_data: the open tasks are scanned once however many agents are drawn",
              len(rows) >= 3 and len(scans) == 1, str((len(rows), len(scans))))
    finally:
        con.close()


def test_assign_offers_recent_targets_then_the_holders_team() -> None:
    _home("assign_team")
    from handline.ui import chat_page as cp
    everyone = ["analytics", "backend", "frontend", "marketing", "reviewer"]
    check("assign to: the holder's own team, then the rest, without the holder",
          actions.assign_targets("backend", everyone) == ([], ["frontend"], ["analytics", "marketing", "reviewer"]))
    check("assign to: a holder in no team has no own team, and everyone is offered",
          actions.assign_targets("human", everyone) == ([], [], everyone))
    check("assign to: recent targets come first, newest first, and are not listed twice; the holder and strangers are dropped",
          actions.assign_targets("backend", everyone, ["marketing", "backend", "nobody", "frontend", "marketing"])
          == (["marketing", "frontend"], [], ["analytics", "reviewer"]))
    agents.register("archivist")
    agents.register("reviewer")
    con = db.connect()
    try:
        ok, _, t1 = actions.task_new("product", "backend", "One", "the work and its done")
        ok, _, t2 = actions.task_new("product", "backend", "Two", "the work and its done")
        check("assign to: a task opened for its holder offers whoever opened it",
              actions.recent_assignees(con, t1) == ["product"], str(actions.recent_assignees(con, t1)))
        said = [actions.assign_task(con, t2, "archivist", "you take it"),
                actions.assign_task(con, t2, "reviewer", "you review it"),
                actions.assign_task(con, t2, "archivist", "again")]
        check("assign to: the handoffs used here went through", all(ok for ok, _ in said), str(said))
        check("assign to: opener, then each agent by when they first took part, each once",
              db.task_holders(con, t2) == ["product", "backend", "archivist", "reviewer"], str(db.task_holders(con, t2)))
        check("assign to: recent is that without the holder now",
              actions.recent_assignees(con, t2) == ["product", "backend", "reviewer"], str(actions.recent_assignees(con, t2)))
        ok, _, t3 = actions.task_new("product", "backend", "Three", "the work and its done")
        con.execute("UPDATE tasks SET created_at = created_at + 5 WHERE id = ?", (t3,))   # the row made a second after its request
        check("assign to: the opener and its owner lead even when the request that asked the owner is older than the row",
              db.task_holders(con, t3) == ["product", "backend"], str(db.task_holders(con, t3)))
        ok, said = actions.task_handoff(con, "archivist", t2, "human", "done, back to you")
        check("assign to: a task that went back to the founder keeps the founder out of recent",
              ok and "human" not in actions.recent_assignees(con, t2)
              and "human" in db.task_holders(con, t2), str((ok, said, db.task_holders(con, t2))))
        actions.assign_task(con, t1, "analytics", "other task, other agent")
        check("assign to: another task's holders are not this task's",
              "analytics" not in actions.recent_assignees(con, t2), "")
    finally:
        con.close()


def test_a_timeline_entry_that_fits_stays_one_line_and_a_longer_one_shows_whole() -> None:
    from handline.ui import chat_page as cp
    entry = {"ts": 1_700_000_000, "who": "reviewer", "kind": "memo", "text": "short", "memo": 1, "edited_at": None}
    one = cp.timeline_text(entry).plain
    many = cp.timeline_text({**entry, "text": "first line\nsecond line\n  third\n"}).plain
    check("timeline: a one-line entry is one line", "\n" not in one and one.endswith("short"), one)
    check("timeline: a several-line entry keeps every line, the later ones indented under the first",
          many.split("\n")[1:] == ["    second line", "      third"] and many.split("\n")[0].endswith("first line"), many)


def test_a_timeline_entry_shows_its_kind_after_the_time_with_an_icon_and_a_colour_of_its_own() -> None:
    from handline.ui import chat_page as cp
    base = {"ts": 1_700_000_000, "who": "reviewer", "memo": None, "edited_at": None}
    memo = cp.timeline_text({**base, "kind": "memo", "text": "a memo"})
    req = cp.timeline_text({**base, "kind": "request", "text": "#7  queued  ask it", "ref": "#7", "state": "queued",
                            "summary": "ask it"})
    check("timeline: the kind follows the time, before who, an icon in front of it",
          "✎ memo  reviewer  a memo" in memo.plain and "↗ request  reviewer  #7  queued  ask it" in req.plain,
          memo.plain + " | " + req.plain)
    state = cp.timeline_text({**base, "kind": "state", "text": "draft -> done"})
    odd = cp.timeline_text({**base, "kind": "new_kind", "text": "x"}).plain
    check("timeline: a status change has its own icon and colour, and a kind the page does not know is still drawn",
          "◆ state  reviewer  draft -> done" in state.plain and "new_kind  reviewer  x" in odd, state.plain + odd)
    colour = lambda t, word: next(str(sp.style) for sp in t.spans if t.plain[sp.start:sp.end].startswith(word))
    check("timeline: a memo and a request differ in colour, and a request's state differs from its text",
          colour(memo, "✎") != colour(req, "↗") and colour(memo, "a memo") != colour(req, "ask it")
          and colour(req, "queued") not in (colour(req, "ask it"), colour(req, "#7")), "")
    states = {s: cp.REQUEST_STATE_STYLE.get(s, "bold green") for s in ("queued", "rejected", "denied", "withdrawn", "replaced", "answered")}
    check("timeline: green is for a request that was answered; one that never reached its target is not green",
          states["answered"] == "bold green" and all("green" not in states[s] for s in ("denied", "withdrawn", "replaced")),
          str(states))


def test_schedules_are_built_read_and_written_through_actions() -> None:
    _home("schedules")
    cron = actions.schedule_cron
    check("schedule: each kind builds its cron",
          [cron("every day", "9", "30"), cron("weekdays", "9", "0"), cron("every week", "18", "5", 1),
           cron("every hour", minute="15"), cron("every N minutes", every="10")]
          == ["30 9 * * *", "0 9 * * 1-5", "5 18 * * 1", "15 * * * *", "*/10 * * * *"])
    for bad in (("every day", "24", "0"), ("every day", "9", "60"), ("every N minutes", "9", "0", 1, "0"),
                ("sometimes", "9", "0")):
        try:
            cron(*bad)
            check(f"schedule: {bad} is refused", False)
        except ValueError:
            check(f"schedule: {bad} is refused", True)
    check("schedule: what it builds reads back in words, and anything else stays as written",
          [actions.schedule_words(c) for c in ("30 9 * * *", "0 9 * * 1-5", "5 18 * * 1", "15 * * * *", "*/10 * * * *", "0 0 1 * *")]
          == ["every day 09:30", "weekdays 09:00", "Mon 18:05", "every hour at :15", "every 10 minutes", "0 0 1 * *"])
    check("schedule: an agent starts with none", actions.schedule_rows("backend") == [])
    actions.schedule_add("backend", cron("weekdays", "9", "0"), "  check   the board ")
    rows = actions.schedule_rows("backend")
    check("schedule: an added one is listed with its next run and a one-line message",
          [(r["cron"], r["message"]) for r in rows] == [("0 9 * * 1-5", "check the board")] and rows[0]["next"] != "never")
    try:
        actions.schedule_add("backend", "not a cron", "x")
        check("schedule: a bad cron is refused", False)
    except ValueError:
        check("schedule: a bad cron is refused", len(actions.schedule_rows("backend")) == 1)
    actions.schedule_edit("backend", 1, "15 8 * * *", "  new   words ")
    check("schedule: an edited one keeps its place with the new cron and message",
          [(r["cron"], r["message"]) for r in actions.schedule_rows("backend")] == [("15 8 * * *", "new words")])
    for bad in (("backend", 2, "15 8 * * *", "x"), ("backend", 1, "not a cron", "x"), ("backend", 1, "15 8 * * *", " ")):
        try:
            actions.schedule_edit(*bad)
            check(f"schedule: editing with {bad[1:]} is refused", False)
        except ValueError:
            check(f"schedule: editing with {bad[1:]} is refused",
                  [r["message"] for r in actions.schedule_rows("backend")] == ["new words"])
    check("schedule: a cron the form makes maps back to its fields, any other to None",
          actions.schedule_fields("5 18 * * 1") == {"kind": "every week", "day": 1, "hour": "18", "minute": "5"}
          and actions.schedule_fields("*/10 * * * *") == {"kind": "every N minutes", "every": "10"}
          and actions.schedule_fields("0 0 1 * *") is None)
    actions.schedule_remove("backend", 1)
    check("schedule: a removed one is gone", actions.schedule_rows("backend") == [])


def test_assign_answers_from_the_task_page() -> None:
    _home("assign")
    agents.register("reviewer")
    con = db.connect()
    try:
        ok, _, tid = actions.task_new(actions.HUMAN, "reviewer", "Fix it", "the work and its done")
        check("assign: a task to answer", ok)
        ok, _, other = actions.task_new("product", "backend", "Other", "the work and its done")
        check("assign: a handoff needs its text", not actions.assign_task(con, other, "reviewer", "")[0])
        ok, said = actions.assign_task(con, other, "reviewer", "you take it")
        check("assign: a handoff moves the task to the one chosen", ok and db.get_task(con, other)["owner"] == "reviewer", said)
    finally:
        con.close()


def test_a_task_timeline_holds_memos_and_handoffs_in_time_order() -> None:
    _home("timeline")
    agents.register("reviewer")
    con = db.connect()
    try:
        ok, _, tid = actions.task_new(actions.HUMAN, "reviewer", "Fix it", "the work and its done")
        actions.memo_add(con, tid, "reviewer", "first")
        actions.memo_add(con, tid, actions.HUMAN, "a memo")
        ok, _, other = actions.task_new("product", "backend", "Other", "the work and its done")
        actions.assign_task(con, other, "reviewer", "you take it")
        tl = actions.task_timeline(con, tid)
        check("timeline: the request that opened the task, a memo and one sent with the task verb (itself a memo) show once each, in order",
              [e["kind"] for e in tl] == ["request", "memo", "memo"] and [e["text"] for e in tl[1:]] == ["first", "a memo"]
              and tl[0]["text"].startswith("#") and all(e["memo"] for e in tl[1:]), str(tl))
        check("timeline: a handoff shows from and to, and its request is not a second entry",
              [(e["kind"], e["who"]) for e in actions.task_timeline(con, other) if e["kind"] != "request"] == [("handoff", "backend → reviewer")]
              and [e["kind"] for e in actions.task_timeline(con, other)].count("request") == 1,
              str(actions.task_timeline(con, other)))
        actions.memo_add(con, other, "reviewer", "after the handoff")
        ok, _, third = actions.task_new("product", "backend", "Third", "the work and its done")
        actions.memo_add(con, third, "product", "before the handoff")
        actions.assign_task(con, third, "reviewer", "you take it")
        actions.memo_add(con, third, "reviewer", "after the handoff")
        kinds = lambda t: [(e["kind"], e["text"]) for e in actions.task_timeline(con, t) if e["kind"] != "request"]
        check("timeline T524: a memo made after a handoff in the same second sits after it, not before",
              [k for k, _ in kinds(other)] == ["handoff", "memo"], str(kinds(other)))
        check("timeline T524: memo, handoff, memo in one second keep the order they happened in",
              [k for k, _ in kinds(third)] == ["memo", "handoff", "memo"]
              and kinds(third)[0][1] == "before the handoff", str(kinds(third)))
    finally:
        con.close()


def test_a_slash_line_is_a_bridge_command_and_never_a_message() -> None:
    from handline.ui import chat_page as cp
    sa = cp.slash_action
    check("slash: plain text is not a command", sa("hello /exit") is None and sa("") is None)
    check("slash: /exit and /help are commands", sa("/exit") == ("run", "exit") and sa(" /help ") == ("run", "help"))
    check("slash: the tab commands still run, with an argument they open or sort",
          sa("/chat") == ("run", "chat") and sa("/task") == ("run", "task") and sa("/task #12") == ("open", 12)
          and sa("/sort owner") == ("sort", "owner"))
    check("slash: an agent verb keeps its rule (move takes an argument, the rest none)",
          sa("/wake") == ("verb", "wake", "") and sa("/move beta") == ("verb", "move", "beta")
          and sa("/wake now")[0] == "error")
    for bad in ("/nothing", "/", "/exit now", "/task x", "/sort colour", "/nothing at all"):
        check(f"slash: {bad!r} is an error, so it is never sent", (sa(bad) or ("",))[0] == "error", str(sa(bad)))
    check("slash: an error says the command", "/nothing" in sa("/nothing")[1])


async def test_restart_is_a_chat_command_with_a_fresh_form() -> None:
    from handline.ui import chat_page as cp
    from handline.ui.components import jobs_aware
    sa = cp.slash_action
    check("restart: /restart and /restart fresh are verbs; anything else after it is an error",
          sa("/restart") == ("verb", "restart", "") and sa("/restart fresh") == ("verb", "restart", "fresh")
          and sa("/restart now")[0] == "error" and "fresh" in sa("/restart now")[1], str(sa("/restart now")))
    offered = [(x.label[0], x.arg) for x in cp.suggest("/", [], [], ("restart",))]
    check("restart: the slash menu lists both forms",
          ("/restart", "restart") in offered and ("/restart fresh", "restart fresh") in offered, str(offered))
    check("restart: a space after it offers fresh",
          [x.arg for x in cp.suggest("/restart ", [], [], ("restart",))] == ["restart fresh"]
          and not cp.suggest("/restart ", [], [], ("wake",)))
    check("restart: it is on offer only where the board offers it", "restart" in cp.CHAT_VERBS)

    home = _home("restart_chat")
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", "IDLE")
        started: list = []
        toasts: list = []
        asked: list = []

        class _App:
            async def push_screen_wait(self, modal):
                asked.append(modal.question)
                return True

        class _Screen:
            app = _App()

            def _start(self, verb, target, what, args, kwargs=None, on_result=None):
                started.append((verb, target, args))
                return what

            def toast(self, msg, ok=True):
                toasts.append(msg)

        for arg, want in (("", ("restart", "backend", ("backend",))),
                          ("fresh", ("restart", "backend", ("backend", True)))):
            started.clear()
            await jobs_aware.dispatch_agent_verb(_Screen(), con, "backend", "restart", arg)
            check(f"restart: /restart {arg} on an idle agent's page starts the restart job".replace("  ", " "),
                  started == [want], f"{started} {toasts}")
        check("restart: it asks first, and says when it will be a new conversation",
              asked == ["restart backend", "restart backend as a new conversation"], str(asked))
        db.set_agent_state(con, "backend", "RUNNING")
        started.clear()
        await jobs_aware.dispatch_agent_verb(_Screen(), con, "backend", "restart", "")
        check("restart: a mid-turn agent is refused on the page, with the reason shown",
              not started and "mid-turn" not in toasts[-1] and "RUNNING" in toasts[-1], str(toasts[-1:]))
    finally:
        con.close()


def test_a_standing_answer_is_added_from_the_settings() -> None:
    home = _home("add_answer")
    folder = home / "work"
    folder.mkdir()
    check("add answer: a folder that is not one is refused", not actions.add_answer(folder="/no/such/folder")[0])
    check("add answer: a half-filled rule is refused", not actions.add_answer(vendor="claude", title="Run it?")[0])
    check("add answer: a rule is kept", actions.add_answer(vendor="claude", title="Run it?", label="Yes")[0]
          and any(a["text"] == 'claude: "Run it?" -> Yes' for a in actions.standing_answers()))
    check("add answer: a folder is trusted", actions.add_answer(folder=str(folder))[0]
          and any(a["kind"] == "trust" for a in actions.standing_answers()))


async def test_a_change_of_the_agents_state_redraws_the_spinner_not_the_chat() -> None:
    """T534: the transcript is torn down only when its rows change; RUNNING/IDLE and the wait
    text update the spare row and the send-now buttons in place, so the chat does not flash."""
    from textual.app import App
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("noflash")
    agents.register("reviewer")
    askmod.ask("reviewer", "hello", requester=actions.HUMAN)

    class Host(App):
        CSS_PATH = cp.Path(cp.__file__).with_name("chat_page.tcss") if hasattr(cp, "Path") else None

    app = Host()
    def screen_text() -> str:
        import html, re
        return html.unescape(re.sub(r"<[^>]*>", "", app.export_screenshot())).replace("\xa0", " ")

    async with app.run_test(size=(100, 30)) as pilot:
        page = cp.ChatPage("reviewer")
        await app.push_screen(page)
        await pilot.pause()
        page._sig = None
        page.refresh_conversation()
        await pilot.pause()
        before = list(page.query(cp.ChatMessage))
        page.agent_state = ("RUNNING", "")
        page._sig = None        # what a flip of the word does (refresh_agent_state)
        page.refresh_conversation()
        await pilot.pause()
        check("noflash: the messages are the same widgets after the agent starts working",
              before and list(page.query(cp.ChatMessage)) == before)
        line = page.query(cp.WorkLine).first()
        check("noflash: and the screen now spins", line.working is True and "working" in screen_text())
        page.agent_state = ("IDLE", "waiting for x's reply (#1)")
        page._sig = None
        page.refresh_conversation()
        await pilot.pause()
        shown = screen_text()
        check("noflash: back to idle with a wait, the screen shows the wait, not 'working'",
              "working" not in shown and "waiting for x's reply (#1)" in shown
              and list(page.query(cp.ChatMessage)) == before)
        page.agent_state = ("IDLE", "waiting for y's reply (#2)")
        page._sig = None
        page.refresh_conversation()
        await pilot.pause()
        check("noflash: a change of the wait text alone shows on the screen",
              "waiting for y's reply (#2)" in screen_text() and list(page.query(cp.ChatMessage)) == before)


async def test_neither_tasks_tab_scrolls_sideways() -> None:
    """T376: the Tasks tab of the home page and of a chat page draw the same table, cut to the room."""
    from textual.widgets import DataTable, Tabs
    from handline.ui import chat_page as cp
    _home("tabsnoscroll")
    agents.register("reviewer")
    con = db.connect()
    try:
        for i in range(4):
            db.new_task(con, "a task with a rather long title that would run off the right edge " * 2,
                        "reviewer", actions.HUMAN)
        con.commit()
    finally:
        con.close()

    from handline.ui.bridge_textual import BridgeApp
    app = BridgeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("T")
        await pilot.pause()
        check("tasks: T on the home page opens the Tasks tab, no screen of its own",
              app.screen.query_one("#scope", Tabs).active == "tasks" and type(app.screen).__name__ == "HomePage",
              type(app.screen).__name__)
        table = app.screen.query_one("#tasks_table", DataTable)
        await pilot.press("down")
        await pilot.pause()
        check("tasks: after T the table has the focus and ↓ moves its cursor",
              app.focused is table and table.cursor_row == 1, f"focused {app.focused} row {table.cursor_row}")
    for width in (80, 100, 140):
        for name in ("home", "chat"):
            app = BridgeApp()
            async with app.run_test(size=(width, 30)) as pilot:
                await pilot.pause()
                page = app.screen
                if name == "chat":
                    page = cp.ChatPage("reviewer")
                    await app.push_screen(page)
                    await pilot.pause()
                page.query_one("#scope", Tabs).active = "tasks"
                await pilot.pause()
                await pilot.pause()
                table = page.query_one("#tasks_table", DataTable)
                check(f"tasks tab ({name}, {width} wide): the table has rows and no sideways scroll",
                      table.row_count > 0 and table.virtual_size.width <= table.size.width,
                      f"rows {table.row_count} virtual {table.virtual_size.width} shown {table.size.width}")


def test_task_changes_are_set_among_the_messages_at_their_time() -> None:
    """Founder #6869: a task in the chat is one line, and each change of state sits between the messages
    at the moment it happened."""
    from handline.ui import chat_page as cp
    _home("taskchanges")
    agents.register("reviewer")
    con = db.connect()
    try:
        t = db.new_task(con, "the work", "reviewer", actions.HUMAN)
        rid = db.add_request(con, actions.HUMAN, "reviewer", "do it", None, None, "fire_and_forget", "task", 0, None)
        db.link_request(con, t, rid)
        for ts, kind in ((200, "task_start"), (300, "mark_task_done"), (400, "reopen_task")):
            db.add_event(con, "reviewer", kind, {"task_id": t})
            con.execute("UPDATE events SET ts = ? WHERE id = (SELECT MAX(id) FROM events)", (ts,))
        con.commit()
        rows = cp.requests_page(con, "reviewer", False)[0]
        msgs = cp.messages_of(rows, "reviewer")
        for m in msgs:
            m["when"], m["unread"] = 100, False
        changes = cp.task_changes(con, msgs)
    finally:
        con.close()
    check("changes: each move of the task is one, from the state before",
          [c["change"] for c in changes] == ["queued -> doing", "doing -> done", "done -> queued"],
          str([c["change"] for c in changes]))
    later = {"who": "you", "when": 250, "unread": False, "rid": 9, "part": 0}
    merged = cp.with_task_changes([*msgs, later], changes)
    check("changes: set among the messages by time",
          [m.get("change") or m["rid"] for m in merged] == [msgs[0]["rid"], "queued -> doing", 9,
                                                            "doing -> done", "done -> queued"],
          str([m.get("change") or m["rid"] for m in merged]))


def _edit_owner(con, tid: int, old: str, new: str):
    """`edit --owner` as every caller runs it: the edit, then the new owner's delivery."""
    ok, said = actions.edit_task(con, tid, actions.HUMAN, owner=new)
    actions.send_moved_task(con, tid, old, actions.HUMAN)
    return ok, said


def test_a_change_names_who_made_it_and_an_assignment_says_to_whom() -> None:
    """Founder #7364: above the card, who changed it; inside, what happened - each written by
    the real action, not a hand-made event."""
    from handline.ui import chat_page as cp
    _home("changewho")
    agents.register("reviewer")
    agents.register("backend")
    con = db.connect()
    try:
        t = db.new_task(con, "the work", "reviewer", actions.HUMAN)
        rid = db.add_request(con, actions.HUMAN, "reviewer", "do it", None, None, "fire_and_forget", "task", 0, None)
        db.link_request(con, t, rid)
        con.commit()
        steps = (lambda: actions.edit_task(con, t, actions.HUMAN, title="the new work"),
                 lambda: actions.edit_task(con, t, actions.HUMAN, priority=1),
                 lambda: actions.task_handoff(con, "reviewer", t, "backend", "yours now"),
                 lambda: _edit_owner(con, t, "backend", "reviewer"))
        for at, step in enumerate(steps, 300):
            ok, said = step()
            check(f"changes: the action itself worked ({at})", ok, said)
            con.execute("UPDATE events SET ts = ? WHERE id = (SELECT MAX(id) FROM events)", (at,))
        con.commit()
        msgs = cp.messages_of(cp.requests_page(con, "reviewer", False)[0], "reviewer")
        for m in msgs:
            m["when"] = 100
        changes = [c for c in cp.task_changes(con, msgs, "reviewer") if c["when"] >= 300]
    finally:
        con.close()
    got = [(c["who"], c["change"]) for c in changes]
    check("changes: each names who made it and says what changed, a handoff and an owner edit say to whom",
          got == [(actions.HUMAN, "title changed"), (actions.HUMAN, "priority P2 -> P1"),
                  ("reviewer", "handed to backend"), (actions.HUMAN, "assigned to reviewer")], str(got))
    check("changes: the founder's is side you (his background), an agent's is not, and a handoff carries its text",
          [c["side"] for c in changes] == ["you", "you", "agent", "you"]
          and [c["note"] for c in changes] == ["", "", "yours now", ""], str(changes))


def test_a_handoff_is_one_card_not_a_changed_task_and_a_new_task() -> None:
    """Founder #8922: the founder handed T to orcapipe; the chat drew CHANGED TASK and NEW TASK."""
    from handline.ui import chat_page as cp
    _home("handoffone")
    agents.register("reviewer")
    agents.register("backend")
    con = db.connect()
    try:
        t = db.new_task(con, "the work", "reviewer", actions.HUMAN)
        ok, said = actions.task_handoff(con, "reviewer", t, "backend", "yours now")
        check("handoff card: the action worked", ok, said)
        con.commit()
        msgs = cp.messages_of(cp.requests_page(con, "backend", True)[0], "backend")
        cp.fold_handoffs(con, msgs)
        changes = cp.task_changes(con, msgs, "backend")
    finally:
        con.close()
    cards = [m for m in msgs if cp.is_card(m)]
    check("handoff card: one card, a change that keeps its delivery state, no second change beside it",
          len(cards) == 1 and not [c for c in changes if c["change"].startswith("handed")]
          and cards[0]["landing"] in ("queued", "delivered"), str((cards, changes)))
    check("handoff card: it reads CHANGED TASK handed to backend with the typed text, never NEW TASK",
          cp.card_lines(cards[0]).plain == f"CHANGED TASK T{t} : the work\nhanded to backend\nyours now",
          cp.card_lines(cards[0]).plain)


async def test_the_chat_page_draws_a_handoff_as_one_card() -> None:
    """T838 through the page: one CHANGED TASK card with its text, no NEW TASK, no change row beside it."""
    from handline.ui import chat_page as cp
    from handline.ui.bridge_textual import BridgeApp
    _home("handoffpage")
    agents.register("reviewer")
    agents.register("backend")
    con = db.connect()
    try:
        t = db.new_task(con, "the work", "reviewer", actions.HUMAN)
        ok, said = actions.task_handoff(con, "reviewer", t, "backend", "yours now")
        check("handoff page: the action worked", ok, said)
        con.commit()
    finally:
        con.close()
    app = BridgeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        cp.scope_file().write_text("all")
        page = cp.ChatPage("backend")
        await app.push_screen(page)
        await pilot.pause()
        shown = [w.query_one(".cardbox").content.plain for w in page.query(cp.ChatMessage) if w.query(".cardbox")]
        rows = list(page.query(cp.TaskChange))
    check("handoff page: one card, CHANGED TASK handed to backend with the note, no NEW TASK, no change row",
          len(shown) == 1 and shown[0] == f"CHANGED TASK T{t} : the work\nhanded to backend\nyours now" and not rows,
          str((shown, rows)))


def test_a_delivered_task_becomes_doing_when_it_landed_with_no_start_event() -> None:
    """T601: `task new`, a handoff and the queue sweep log no start; the delivery's landing says when."""
    from handline.ui import chat_page as cp
    _home("landeddoing")
    agents.register("reviewer")
    con = db.connect()
    try:
        t = db.new_task(con, "the work", "reviewer", actions.HUMAN)
        rid = db.add_request(con, actions.HUMAN, "reviewer", "do it", None, None, "fire_and_forget", "task", 0, None)
        db.link_request(con, t, rid)
        con.execute("UPDATE requests SET landed_at = 500 WHERE id = ?", (rid,))
        db.add_event(con, "reviewer", "mark_task_done", {"task_id": t})
        con.execute("UPDATE events SET ts = 600 WHERE id = (SELECT MAX(id) FROM events)")
        con.commit()
        msgs = cp.messages_of(cp.requests_page(con, "reviewer", False)[0], "reviewer")
        changes = cp.task_changes(con, msgs)
    finally:
        con.close()
    check("changes: a delivery that landed is doing from then, before done",
          [(c["change"], c["when"]) for c in changes] == [("queued -> doing", 500), ("doing -> done", 600)],
          str([(c["change"], c["when"]) for c in changes]))


async def test_the_chat_page_draws_a_changed_task_as_a_one_line_card() -> None:
    from textual.color import Color
    from handline.ui import chat_page as cp
    from handline.ui.bridge_textual import BridgeApp
    _home("changecard")
    agents.register("reviewer")
    con = db.connect()
    try:
        t = db.new_task(con, "the work", "reviewer", actions.HUMAN)
        rid = db.add_request(con, actions.HUMAN, "reviewer", "do it", None, None, "fire_and_forget", "task", 0, None)
        db.link_request(con, t, rid)
        db.add_event(con, "reviewer", "task_start", {"task_id": t})
        actions.edit_task(con, t, actions.HUMAN, priority=1)
        con.commit()
    finally:
        con.close()
    app = BridgeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        page = cp.ChatPage("reviewer")
        await app.push_screen(page)
        await pilot.pause()
        changes = list(page.query(cp.TaskChange))
        cards = [(w.query_one(".cardhead").content.plain, w.query_one(".cardbox").content.plain) for w in changes]
        check("chat: a task's change is a card of its own, who above, CHANGED TASK and the change inside",
              len(cards) == 2 and "queued -> doing" in cards[0][1] and cards[0][1].startswith("CHANGED TASK")
              and "reviewer" not in cards[0][1], str(cards))
        agent, founder = changes      # the agent's change came first
        check("chat: the founder's change is drawn as his own (class me), the page agent's as theirs (them)",
              founder.has_class("me") and not founder.has_class("them") and agent.has_class("them")
              and not agent.has_class("me"), str((founder.classes, agent.classes)))
        success = Color.parse(app.theme_variables["success"])
        check("chat: the agent's head is in the success colour, not the founder's default",
              agent.query_one(".cardhead").styles.color.rgb == success.rgb
              and founder.query_one(".cardhead").styles.color.rgb != success.rgb,
              str((agent.query_one(".cardhead").styles.color, success)))


async def test_going_home_from_a_chat_with_a_draft_leaves_home_answering_keys() -> None:
    """The Back button with a draft asks first; after y the chat is gone and Home takes keys (it hung)."""
    from handline.ui import chat_page as cp
    from handline.ui.bridge_textual import BridgeApp
    _home("leave_draft")
    agents.register("reviewer")
    app = BridgeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        home = app.screen
        home.open_chat("reviewer")
        await pilot.pause()
        page = app.screen
        page.query_one("#compose", cp.ChatBox).text = "hi"
        page.query_one("#leave").press()
        await pilot.pause()
        check("leave: a draft in the box asks first", isinstance(app.screen, Confirm), str(app.screen_stack))
        await pilot.press("y")
        await pilot.pause()
        await pilot.pause()
        check("leave: y goes back to the home screen alone", app.screen is home and len(app.screen_stack) == 2,
              str(app.screen_stack))
        await pilot.pause(2.5)      # the chat's own timers (0.1s tick, 2s poll) run on after it is gone
        await pilot.press("?")
        await pilot.pause()
        check("leave: home takes keys again (? opens its key help)", type(app.screen).__name__ == "KeyHelp",
              str(app.screen_stack))


async def test_confirm_ask_runs_its_action_only_on_yes_and_from_the_app() -> None:
    """Confirm.ask: y runs the action, n and Escape do not, and the action is the app's to run."""
    from handline.ui.bridge_textual import BridgeApp
    _home("confirm_ask")
    app = BridgeApp()
    ran: list[str] = []
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        for key in ("n", "escape", "y"):
            Confirm.ask(app, lambda: ran.append("ran"), "Do it")
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
            await pilot.pause()
            check(f"confirm ask: {key} leaves the screen it was over", not isinstance(app.screen, Confirm))
        check("confirm ask: the action ran once, for the y only", ran == ["ran"], str(ran))


async def test_a_chat_clears_its_agents_for_you_and_opens_on_an_old_message() -> None:
    """T665: what the chat draws leaves FOR YOU, and only that; a message older than one page is
    paged back to and drawn."""
    from handline.ui import chat_page as cp
    from handline.ui.bridge_textual import BridgeApp
    _home("foryou_chat")
    agents.register("reviewer")
    con = db.connect()
    try:
        def say(text: str) -> int:
            return db.add_request(con, "reviewer", actions.HUMAN, text, None, "peer", "async", None, 0, None)
        old = say("the OLD one")
        for i in range(cp.PAGE * 2):
            say(f"filler {i}")
        con.commit()
        check("for-you chat: the agent's messages are listed before its chat is opened",
              any(m["from"] == "reviewer" for m in actions.messages(con)))
    finally:
        con.close()
    app = BridgeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        page = cp.ChatPage("reviewer", (old, 0))
        await app.push_screen(page)
        await pilot.pause()
        con = db.connect()
        try:
            check("for-you chat: opening it empties that agent's rows from FOR YOU",
                  not [m for m in actions.messages(con) if m["from"] == "reviewer"])
            late = db.add_request(con, "reviewer", actions.HUMAN, "said while open", None, "peer",
                                         "async", None, 0, None)
            con.commit()
            check("for-you chat: a message that arrived after what was drawn is still listed",
                  [m["id"] for m in actions.messages(con) if m["from"] == "reviewer"] == [late])
        finally:
            con.close()
        page.refresh_conversation(force=True)
        await pilot.pause()
        con = db.connect()
        try:
            check("for-you chat: once drawn it is seen too",
                  not [m for m in actions.messages(con) if m["from"] == "reviewer"])
        finally:
            con.close()
        drawn = [w.m["rid"] for w in page.query(cp.ChatMessage)]
        check("for-you chat: a message older than one page is drawn", old in drawn, str(drawn[:3]))


async def test_the_schedule_form_builds_the_cron_and_the_list_removes_the_row_pressed() -> None:
    import html, re
    from textual.app import App
    from textual.widgets import Select
    from handline.ui import chat_page as cp
    from handline.ui.components.ime import ImeInput
    from handline.ui.components.schedule_add import ScheduleAdd
    from handline.ui.components.schedules_section import SchedulesSection
    _home("schedules_ui")
    actions.schedule_add("backend", "0 9 * * *", "first one")
    actions.schedule_add("backend", "30 18 * * 1-5", "second one")

    class Host(App):
        CSS_PATH = cp.Path(cp.__file__).with_name("chat_page.tcss")

        def compose(self):
            yield SchedulesSection("backend")

    app = Host()

    def screen_text() -> str:
        return html.unescape(re.sub(r"<[^>]*>", "", app.export_screenshot())).replace("\xa0", " ")

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        check("schedules ui: both schedules are listed, in words", "every day 09:00" in screen_text()
              and "weekdays 18:30" in screen_text() and "first one" in screen_text() and "second one" in screen_text())
        got: list = []
        form = ScheduleAdd()
        await app.push_screen(form, got.append)
        await pilot.pause()
        form.query_one("#sa_kind", Select).value = "every week"
        await pilot.pause()
        form.query_one("#sa_hour", ImeInput).value = "18"
        form.query_one("#sa_minute", ImeInput).value = "05"
        form.query_one("#sa_message", ImeInput).value = "  weekly   note "
        check("schedules ui: 'every week' shows the day", form.query_one("#sa_day_row").display)
        form.query_one("#sa_kind", Select).value = "every N minutes"
        await pilot.pause()
        check("schedules ui: 'every N minutes' hides the time and shows the interval",
              not form.query_one("#sa_time_row").display and form.query_one("#sa_every_row").display)
        form.query_one("#sa_kind", Select).value = "every hour"
        await pilot.pause()
        check("schedules ui: 'every hour' keeps the minute and hides the hour",
              form.query_one("#sa_time_row").display and not form.query_one("#sa_hour").display)
        form.query_one("#sa_kind", Select).value = "every week"
        await pilot.pause()
        await pilot.click("#sa_add")
        await pilot.pause()
        check("schedules ui: Add dismisses with the cron built from the fields and a one-line message",
              got == [("5 18 * * 1", "weekly note")], str(got))
        await pilot.click("#sc_ed_2")
        await pilot.pause()
        form = app.screen
        check("schedules ui: editing opens the form on that schedule",
              isinstance(form, ScheduleAdd) and form.query_one("#sa_kind", Select).value == "weekdays"
              and form.query_one("#sa_hour", ImeInput).value == "18" and form.query_one("#sa_minute", ImeInput).value == "30"
              and form.query_one("#sa_message", ImeInput).value == "second one")
        form.query_one("#sa_message", ImeInput).value = "second, reworded"
        await pilot.click("#sa_add")
        await pilot.pause()
        check("schedules ui: Save changes that schedule only, on disk and on screen",
              [(r["cron"], r["message"]) for r in actions.schedule_rows("backend")]
              == [("0 9 * * *", "first one"), ("30 18 * * 1-5", "second, reworded")]
              and "second, reworded" in screen_text(), str(actions.schedule_rows("backend")))
        actions.schedule_add("backend", "0 9-17 * * 1-5", "hourly in office hours")
        section = app.screen_stack[0].query_one(SchedulesSection)
        section.load()
        await pilot.pause()
        await pilot.click("#sc_ed_3")
        await pilot.pause()
        form = app.screen
        check("schedules ui: a time the form cannot build is offered as kept, as it is",
              isinstance(form, ScheduleAdd) and form.query_one("#sa_kind", Select).value == "custom"
              and not form.query_one("#sa_time_row").display)
        form.query_one("#sa_message", ImeInput).value = "office hours, reworded"
        await pilot.click("#sa_add")
        await pilot.pause()
        check("schedules ui: editing only the message of such a schedule keeps its cron",
              [(r["cron"], r["message"]) for r in actions.schedule_rows("backend")][2:]
              == [("0 9-17 * * 1-5", "office hours, reworded")], str(actions.schedule_rows("backend")))
        actions.schedule_remove("backend", 3)
        section.load()
        await pilot.pause()
        await pilot.click("#sc_rm_2")
        await pilot.pause()
        await pilot.click("#yes")
        await pilot.pause()
        left = actions.schedule_rows("backend")
        check("schedules ui: the row pressed is the one removed, on disk and on screen",
              [r["message"] for r in left] == ["first one"] and "first one" in screen_text()
              and "second, reworded" not in screen_text(), str(left))


async def test_a_blocked_task_says_so_where_working_would_be() -> None:
    """T563: an idle agent whose current task is blocked shows `blocked by T<n>` on the work line;
    working and a wait for a reply come first."""
    import html, re
    from handline.ui import tui
    from handline.ui import chat_page as cp
    from handline.ui.bridge_textual import BridgeApp
    _home("blockedline")
    agents.register("reviewer")
    con = db.connect()
    try:
        dep = db.new_task(con, "the one it waits on", "backend", actions.HUMAN)
        t = db.new_task(con, "blocked work", "reviewer", actions.HUMAN)
        db.set_current_task(con, "reviewer", t)
        db.set_agent_state(con, "reviewer", "IDLE")
        check("blocked: not blocked, nothing to say", tui.screen_state(con, db.get_agent_state(con, "reviewer"))[1] == "")
        db.add_block(con, t, dep)
        con.commit()
        state = tui.screen_state(con, db.get_agent_state(con, "reviewer"))
        check("blocked: an idle agent whose task is blocked says by which", state == ("IDLE", f"blocked by T{dep} (backend)"), str(state))
    finally:
        con.close()
    app = BridgeApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        page = cp.ChatPage("reviewer")
        await app.push_screen(page)
        await pilot.pause()
        page.agent_state = state
        page._sig = None
        page.refresh_conversation()
        await pilot.pause()
        shown = html.unescape(re.sub(r"<[^>]*>", "", app.export_screenshot())).replace("\xa0", " ")
        check("blocked: the work line shows it", f"blocked by T{dep} (backend)" in shown and "working" not in shown)
        line = page.query_one(cp.WorkLine)
        links = [sp.style.meta["@click"] for sp in line.render().spans if getattr(sp.style, "meta", None)]
        check("blocked: the T<n> on the work line is a link that opens that task",
              links == [f"screen.open_task({dep})"], str(links))
        await pilot.click(cp.WorkLine, offset=(f" blocked by T{dep}".index(f"T{dep}") + 1, 0))
        await pilot.pause()
        check("blocked: clicking it opens the task page", type(app.screen).__name__ == "TaskDetail",
              type(app.screen).__name__)
        await pilot.press("escape")
        await pilot.pause()
        page.agent_state = ("RUNNING", "")
        page._sig = None
        page.refresh_conversation()
        await pilot.pause()
        shown = html.unescape(re.sub(r"<[^>]*>", "", app.export_screenshot())).replace("\xa0", " ")
        check("blocked: working comes first", "working" in shown and f"blocked by T{dep}" not in shown)


def test_a_task_page_shows_every_line_of_a_multi_line_task() -> None:
    """T600 (founder #6977): the title is the first line; the rest of what the task was given is its brief."""
    from handline.ui import chat_page as cp
    _home("multiline")
    agents.register("reviewer")
    body = "the title\n  cd somewhere && run it\n\nA. second part\n 1. check this"
    con = db.connect()
    try:
        t = db.new_task(con, "the title", "reviewer", actions.HUMAN)
        rid = db.add_request(con, actions.HUMAN, "reviewer", body, None, None, "fire_and_forget", "task", 0, None)
        db.link_request(con, t, rid)
        con.commit()
        d = cp.task_detail(con, t)
    finally:
        con.close()
    lines = cp.task_page(d)["brief"].plain.split("\n")
    check("brief: the lines after the title are all there, the title not twice",
          lines == ["  cd somewhere && run it", "", "A. second part", " 1. check this"], str(lines))


async def test_the_shared_task_view_draws_the_brief() -> None:
    """T600: TaskBody, the one view of a task, draws the brief under the badge."""
    import html, re
    from handline.ui import chat_page as cp
    from handline.ui.bridge_textual import BridgeApp
    _home("briefview")
    agents.register("reviewer")
    con = db.connect()
    try:
        t = db.new_task(con, "the title", "reviewer", actions.HUMAN)
        rid = db.add_request(con, actions.HUMAN, "reviewer", "the title\nsecond line\nthird line", None, None,
                             "fire_and_forget", "task", 0, None)
        db.link_request(con, t, rid)
        con.commit()
    finally:
        con.close()
    app = BridgeApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await app.push_screen(cp.TaskDetail(t))
        await pilot.pause()
        brief = app.screen.query_one("#t_brief")
        check("brief: the task page shows the lines after the title", "second line" in str(brief.render())
              and "third line" in str(brief.render()) and brief.display, str(brief.render()))
        body = app.screen.query_one(cp.TaskBody)
        body.show(None)
        await pilot.pause()
        shown = html.unescape(re.sub(r"<[^>]*>", "", app.export_screenshot())).replace("\xa0", " ")
        check("brief: showing no task takes the last task's brief off the screen",
              "no task" in shown and "second line" not in shown, shown[:200])
        body.show(99999)
        await pilot.pause()
        shown = html.unescape(re.sub(r"<[^>]*>", "", app.export_screenshot())).replace("\xa0", " ")
        check("brief: a task that is gone leaves no brief either", "second line" not in shown)


def test_the_founders_reply_to_a_waiting_message_draws_its_link() -> None:
    """T623 (founder #7145/#7151): the founder's reply to an agent's say names the message it answers.
    A say to him is born landed (T781), so the reply is a say of its own to the agent, with the
    message as its parent - not the verdict of a queued row."""
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("replylink")
    agents.register("reviewer")
    ok, _, said = askmod.say(actions.HUMAN, "anything for me?", requester="reviewer")
    con = db.connect()
    try:
        ok, _ = cp.send_reply(con, "reviewer", said, "yes, this")
        rows, _ = cp.requests_page(con, "reviewer", False)
        msgs = cp.messages_of(rows, "reviewer")
        reply = next(m for m in msgs if m["text"] == "yes, this")
        check("reply link: the founder's reply is a reply to the message it answers",
              ok and reply["reply_to"] == said, str(msgs))
        check("reply link: it is a row of its own to the agent, the original untouched",
              db.get_request(con, said)["state"] == "landed" and db.get_request(con, reply["rid"])["target"] == "reviewer",
              str(reply))
        check("reply link: it is drawn with the link", cp.message_text(reply, True, False, "me").plain
              .split("\n")[1].startswith(f"↩ #{said}"), cp.message_text(reply, True, False, "me").plain)
    finally:
        con.close()


def test_the_founders_reply_to_a_report_draws_its_link() -> None:
    """T623 (founder #7738): a reply to an --fyi say, which asked nothing, names it too."""
    from handline.team import ask as askmod
    from handline.ui import chat_page as cp
    _home("replyreport")
    agents.register("reviewer")
    ok, _, said = askmod.say(actions.HUMAN, "done, FYI", requester="reviewer")
    con = db.connect()
    try:
        ok, sent = cp.send_reply(con, "reviewer", said, "thanks, one question")
        rows, _ = cp.requests_page(con, "reviewer", False)
        msgs = cp.messages_of(rows, "reviewer")
        mine = [m for m in msgs if m["text"] == "thanks, one question"]
        check("reply link: a reply to a report is drawn once, as a reply to it",
              ok and len(mine) == 1 and mine[0]["reply_to"] == said, f"{sent} {msgs}")
        check("reply link: and drawn with the link", mine and cp.message_text(mine[0], True, False, "me").plain
              .split("\n")[1].startswith(f"↩ #{said}"), str(mine))
    finally:
        con.close()


async def test_a_drag_on_a_picked_message_copies_what_was_dragged_and_does_not_unpick_it():
    from textual.screen import Screen
    from handline.ui import chat_page as cp
    m = {"who": "you", "when": 1, "rid": 9, "part": 0, "side": "you", "waiting": False, "replyable": True,
         "answered": False, "queued": False, "delivered": True,
         "text": "hello world again and again until the line wraps on this narrow screen"}

    class Chat(Screen):
        replying = None

        def compose(self):
            yield cp.ChatMessage(m)

        def reply_to(self, msg):
            if self.replying is not None:
                self.replying.show_id(False)
            self.replying = msg
            if msg is not None:
                msg.show_id(True)

    class Host(ClipboardApp):
        def on_mount(self):
            self.push_screen(Chat())

    app = Host()
    async with app.run_test(size=(30, 12)) as pilot:
        await pilot.pause()
        msg = app.screen.query_one(cp.ChatMessage)
        body = msg._text
        app.screen.reply_to(msg)   # picked: the head line sits above the body, so the body starts on row 1
        await pilot.pause()
        await pilot.mouse_down(body, offset=(0, 1))
        await pilot.hover(body, offset=(5, 1))
        await pilot.mouse_up(body, offset=(5, 1))
        app.screen._forward_event(events.Click(body, 5, 1, 5, 1, 1, False, False, False))   # once, as App.on_event does after a mouse-up
        await pilot.pause()
        check("selection: a drag on a picked message copies exactly the dragged text",
              app.screen.get_selected_text() == "hello ", repr(app.screen.get_selected_text()))
        check("selection: and the end of the drag leaves it picked, so the head line stays where the drag was made",
              app.screen.replying is msg)


async def test_a_widget_that_cannot_read_its_selection_copies_nothing_and_the_app_lives():
    from textual.geometry import Offset
    from textual.selection import Selection
    from textual.widgets import Static

    class Host(ClipboardApp):
        def compose(self):
            yield Static("one line", id="s")

    app = Host()
    async with app.run_test(size=(30, 6)) as pilot:
        await pilot.pause()
        app.screen.selections = {app.query_one("#s", Static): Selection(Offset(1, 1), Offset(5, 1))}
        app._copy_selection()
        check("selection: a selection past a widget's lines copies nothing and does not raise", True)


def test_a_draft_is_marked_done_by_whoever_may_mark_it_done_and_the_event_says_it_was_a_draft() -> None:
    from handline.ui import chat_page as cp
    _home("draftdone")
    con = db.connect()
    try:
        mine = db.new_task(con, "mine", "backend", actions.HUMAN)
        theirs = db.new_task(con, "theirs", "backend", "orcapipe")
        done = db.new_task(con, "done", "backend", actions.HUMAN)
        con.execute("UPDATE tasks SET done_evidence = 'x', done_at = 1 WHERE id = ?", (done,))
        con.commit()
        can = {tid: cp.task_detail(con, tid)["can_done"] for tid in (mine, theirs, done)}
        check("done: the page offers it on a draft, whoever owns it, not on one already done",
              can == {mine: True, theirs: True, done: False} and cp.task_detail(con, mine)["state"] == "draft", str(can))
        ok, said = actions.mark_task_done(con, theirs, "reviewer", reason="x")
        check("done: an agent that neither owns the draft nor is the target of a request on it is refused",
              not ok and "can only be marked done" in said, said)
        ok, said = actions.mark_task_done(con, theirs, "backend", reason="its owner may")
        check("done: its owner may mark a draft done", ok, said)
        ok, said = actions.mark_task_done(con, mine, actions.HUMAN, reason="the sub-tasks did it")
        check("done: and the founder, on a draft with no request out", ok, said)
        event = json.loads(con.execute("SELECT payload FROM events WHERE kind = 'mark_task_done' "
                                       "AND agent = ?", (actions.HUMAN,)).fetchone()["payload"])
        check("done: the event records that it left a draft", event["from"] == "draft", str(event))
        line = [e for e in actions.task_timeline(con, mine) if e["kind"] == "state"]
        check("done: and the task's timeline reads draft -> done, by the founder",
              [(e["text"], e["who"]) for e in line] == [("draft -> done", actions.HUMAN)], str(line))
    finally:
        con.close()


async def test_the_task_page_sends_a_draft_straight_to_done_from_its_button_and_its_key() -> None:
    from textual.widgets import Button
    from handline.ui import chat_page as cp
    from handline.ui.bridge_textual import BridgeApp
    _home("draftdonebtn")
    for how in ("click", "x"):
        con = db.connect()
        try:
            tid = db.new_task(con, f"asked, done by its sub-tasks ({how})", "backend", actions.HUMAN)
            con.commit()
        finally:
            con.close()
        app = BridgeApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.push_screen(cp.TaskDetail(tid))
            await pilot.pause()
            check(f"done ({how}): a draft's page offers it", not app.screen.query_one("#done", Button).disabled)
            seen: list = []
            app.screen.query_one(cp.TaskBody).notify = lambda said, **kw: seen.append(said)
            if how == "click":
                await pilot.click("#done")
            else:
                await pilot.press("x")
            await pilot.pause()
            app.screen.query_one("#box").value = "the sub-tasks did it"
            await pilot.press("enter")
            await pilot.pause()
            con = db.connect()
            try:
                row = db.get_task(con, tid)
                check(f"done ({how}): the draft is done with the typed reason, by the founder",
                      row["done_evidence"] == "the sub-tasks did it" and actions.task_state(con, row) == "done",
                      str(seen))
            finally:
                con.close()
            check(f"done ({how}): and the page no longer offers it", app.screen.query_one("#done", Button).disabled)


def test_every_timeline_kind_the_task_page_can_get_is_drawn() -> None:
    from handline.ui import chat_page as cp
    _home("timelinekinds")
    con = db.connect()
    try:
        t = db.new_task(con, "kinds", "backend", actions.HUMAN)
        actions.memo_add(con, t, "backend", "a memo")
        actions.mark_task_done(con, t, actions.HUMAN, reason="x")
        kinds = {e["kind"] for e in actions.task_timeline(con, t)}
    finally:
        con.close()
    drawn = [getattr(cp, name, None) for name in ("TIMELINE_WORD", "TIMELINE_ICON", "TIMELINE_STYLE")]
    missing = {k for k in kinds | {"memo", "request", "handoff", "edit", "state"} for table in drawn
               if table is not None and k not in table}
    check("timeline: every kind has a word, and an icon and colour where the page draws them",
          not missing and "state" in kinds, str(missing))


def test_the_stall_watchdog_writes_the_stacks_and_the_recovery() -> None:
    """T788: a loop silent past STALL_S is logged with every thread's stack, again each second it
    stays blocked, and once when it answers; `timed` and `crumb` leave a line; `--pid` finds no bridge."""
    import asyncio
    from handline.ui import stall
    home = _home("stall")
    loop = asyncio.new_event_loop()
    dog = stall.Watchdog(loop)
    dog.last = 100.0
    dog.check(100.2)
    dog.check(100.9)
    dog.check(101.2)
    dog.check(101.5)
    dog.last = 101.6
    dog.check(101.7)
    loop.close()
    with stall.timed("a read"):
        pass
    text = stall.log_file().read_text()
    check("stall: a silent loop is logged with the stacks, once a second, and its recovery",
          text.count("STALL: event loop silent") == 2 and "[event loop]" in text and "RECOVERED" in text, text[:300])
    check("stall: a timed block leaves its ms", "· a read: " in text and " ms" in text)
    check("stall: no pid file, no bridge", stall.running_pid() is None)


async def test_the_stall_log_never_holds_what_is_typed_and_the_pid_file_is_held_by_the_bridge() -> None:
    """T788 (auditor #8657): a printable key in an open box is logged as "printable", a named key by
    name, and nothing is logged with no box open; `install` writes the pid file and the USR1 dump,
    the bridge holds a lock on the file, so a file left behind (even by kill -9) or one naming a
    reused pid names no bridge (T800)."""
    import signal
    from handline.ui import stall
    from handline.ui.bridge_textual import BridgeApp
    from handline.ui.components.task_assign import TaskAssign
    from handline.cli import bridge as clibridge
    import argparse
    _home("stall_ui")
    app = BridgeApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        check("stall: the title is Handline with no pid, the pid file names this process and it is a screen",
              app.title == "Handline" and stall.pid_file().read_text().strip() == str(os.getpid())
              and stall.running_pid() == os.getpid())
        await pilot.press("tab")
        await pilot.pause()
        check("stall: no box open, no key line", "key " not in stall.log_file().read_text())
        app.push_screen(TaskAssign("T1", "backend", ["analytics"]))
        await pilot.pause()
        await pilot.press(*"Secret pw 42!")
        await pilot.press("tab", "escape")
        await pilot.pause()
        keys = set(re.findall(r"key (\S+) focus", stall.log_file().read_text()))
        check("stall: typed characters are never in the log, a named key is",
              {"printable", "tab", "escape"} <= keys and not keys & (set("Secrpw42") | {"space", "exclamation_mark"}), str(keys))
        os.kill(os.getpid(), signal.SIGUSR1)
        await asyncio.sleep(0.2)
        check("stall: USR1 writes the stacks into the log", "Current thread" in stall.log_file().read_text()
              or "Thread 0x" in stall.log_file().read_text())
        out = _capture_out(lambda: clibridge.cmd_bridge(argparse.Namespace(once=False, textual=False, pid=True, dev_mode=False)))
        check("stall: while the bridge runs, `hl bridge --pid` prints its pid", out == (0, f"{os.getpid()}\n"), str(out))
    os.close(stall._pid_fd)
    stall._pid_fd = None
    check("stall: once the bridge's lock is gone, the file names no bridge", stall.running_pid() is None)
    child = subprocess.Popen([sys.executable, "-c", "import sys, time; sys.path.insert(0, sys.argv[1]); "
                              "from handline.ui import stall; stall._claim_pid_file(__import__('os').getpid()); print('up', flush=True); time.sleep(60)",
                              str(ROOT)], stdout=subprocess.PIPE, text=True)
    try:
        child.stdout.readline()
        check("stall: another process's bridge lock names that process", stall.running_pid() == child.pid, str(stall.running_pid()))
    finally:
        child.kill()
        child.wait()
    check("stall: a bridge killed by a signal leaves a file that names no bridge", stall.running_pid() is None)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        stall.pid_file().write_text(f"{sleeper.pid}\n")
        out = _capture_out(lambda: clibridge.cmd_bridge(argparse.Namespace(once=False, textual=False, pid=True, dev_mode=False)))
        check("stall: a pid file naming a live process that is no bridge (a reused pid) names none",
              stall.running_pid() is None and out[0] == 1, str(out))
    finally:
        sleeper.kill()
        sleeper.wait()


def test_a_second_bridge_says_so_and_offers_to_take_over() -> None:
    _home("second_bridge")
    from handline.ui import stall
    stall.log_file().parent.mkdir(parents=True, exist_ok=True)
    code = ("import sys, time; sys.path.insert(0, sys.argv[1]); from handline.ui import stall; "
            "stall._claim_pid_file(__import__('os').getpid()); print('up', flush=True); time.sleep(60)")

    def old_bridge() -> subprocess.Popen:
        child = subprocess.Popen([sys.executable, "-c", code, str(ROOT)], stdout=subprocess.PIPE, text=True)
        child.stdout.readline()
        return child

    def answer(word: str):
        asked = []
        return asked, lambda q: (asked.append(q), word)[1]

    check("second bridge: with none running it just starts", bt._one_bridge(lambda q: "q", tty=True) is True)
    child = old_bridge()
    try:
        asked, ask = answer("q")
        out = _capture_out(lambda: bt._one_bridge(ask, tty=True))
        check("second bridge: it names the running pid and offers take over or quit",
              str(child.pid) in out[1] and "take over" in asked[0] and "quit" in asked[0], str((out, asked)))
        check("second bridge: quit leaves the old one running and does not start", child.poll() is None
              and stall.running_pid() == child.pid and bt._one_bridge(ask, tty=True) is False)
        asked, ask = answer("t")
        out = _capture_out(lambda: bt._one_bridge(ask, tty=False))
        check("second bridge: with no terminal on stdin it names the pid, asks nothing and does not start",
              out[1].count(str(child.pid)) == 1 and out[0] is False and not asked and child.poll() is None, str((out, asked)))
        check("second bridge: a bridge that cannot take the lock says so and holds none",
              stall._claim_pid_file(os.getpid()) is False and stall._pid_path != stall.pid_file())
        started = bt._one_bridge(ask, tty=True)
        child.wait(timeout=5)
        check("second bridge: take over closes the old one and starts", started is True
              and stall.running_pid() is None, str((started, stall.running_pid())))
    finally:
        child.kill()
        child.wait()


def _capture_out(fn) -> "tuple[int, str]":
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fn()
    return code, buf.getvalue()


def test_a_late_answer_still_counts_as_new_everywhere() -> None:
    """T835 step 4a: the answering say can land long after the request entered its state (the line was
    delivered, the answer came later). Everything that asks "what changed" reads the say's own time too."""
    from handline.ui import chat_page as cp
    from handline.team import ask as askmod
    home = _home("lateans")
    agents.register("reviewer")
    con = db.connect()
    try:
        long_ago = int(time.time()) - 5000
        ok, msg, rid = askmod.ask("reviewer", "how is it?", requester=actions.HUMAN)
        askmod.resolve(rid, "fine")
        said = db.answer_row_of(con, rid)["id"]
        con.execute("UPDATE requests SET ts = ?, state_at = ? WHERE id = ?", (long_ago - 10, long_ago, rid))
        con.execute("UPDATE requests SET ts = ? WHERE id = ?", (long_ago, said))
        tid = db.new_task(con, "watched", "reviewer", actions.HUMAN)
        db.link_request(con, tid, rid)
        con.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (long_ago - 20, tid))
        seen = long_ago + 100
        db.set_cursor(con, "seen:reviewer", seen)
        before = (actions.chat_unread(con, "reviewer"), cp.signature(con, "reviewer", False),
                  db.task_last_activity(con, tid))
        check("late answer: before it lands, nothing is new", before[0] == 0 and before[2] == long_ago, str(before))
        late = int(time.time()) + 900       # after everything else in the chat, as a signature's maximum needs
        con.execute("UPDATE requests SET ts = ? WHERE id = ?", (late, said))      # the same say, answered later
        check("late answer: chat_unread counts it although the request's state_at is older than the tab's last look",
              actions.chat_unread(con, "reviewer") == 1)
        check("late answer: the chat signature moves", cp.signature(con, "reviewer", False) != before[1])
        check("late answer: the task's last activity is the say's time", db.task_last_activity(con, tid) == late,
              str(db.task_last_activity(con, tid)))
        row = next((r for r in actions.task_rows(con) if r["id"] == tid), None)
        check("late answer: and so is the board's batch reading of it", row is not None and row["last_activity"] == late)
        check("late answer: and the chat's own task list", [x for x in cp.tasks_of(con, "reviewer") if x[0] == tid][0][5] == late)
    finally:
        con.close()


def test_a_bang_line_runs_in_zsh_in_the_worktree_and_an_agent_cannot_run_one() -> None:
    from handline.infra import protected
    _home("bang")
    check("bang: !ls is the command ls", actions.command_of("!ls -l") == "ls -l" and actions.command_of("  ! pwd ") == "pwd")
    check("bang: a bare ! or plain text is not one", actions.command_of("!") is None and actions.command_of("hi !ls") is None)
    with tempfile.TemporaryDirectory() as d:
        run = actions.ShellRun("echo out; echo err >&2; pwd; exit 3", d)
        for _ in range(100):
            if not run.running:
                break
            time.sleep(0.05)
        lines, dropped, code = run.snapshot()
        check("bang: stdout and stderr are both kept, and the exit code", code == 3 and lines[:2] == ["out", "err"], str((lines, code)))
        check("bang: it ran in the given directory", Path(lines[2]).resolve() == Path(d).resolve(), lines[2])
        slow = actions.ShellRun("sleep 30", d)
        slow.stop()
        for _ in range(100):
            if not slow.running:
                break
            time.sleep(0.05)
        check("bang: stop ends a running command", not slow.running and slow.stopped and slow.code != 0, str(slow.code))
        big = actions.ShellRun("seq 1 25000", d)
        for _ in range(100):
            if not big.running:
                break
            time.sleep(0.05)
        lines, dropped, _ = big.snapshot()
        check("bang: a long output keeps its end and counts what it let go",
              lines[-1] == "25000" and dropped == 25000 - len(lines) > 0, f"{len(lines)} {dropped}")
        msg, saved = big.as_message(), big.save_output()
        check("bang: what goes to the agent is the command and the path of a file holding the output",
              "seq 1 25000" in msg and str(saved) in msg and "exit 0" in msg and saved.read_text().splitlines()[-1] == "25000" and "earlier lines left out" in saved.read_text().splitlines()[0],
              msg)
        check("bang: a second save is the same file", big.save_output() == saved)
    cwd = actions.cwd_for(db.connect(), None)
    check("bang: with no agent it runs in home", cwd == str(Path.home()), cwd)
    os.environ["OCP_AGENT"] = "backend"
    try:
        actions.ShellRun("echo no", cwd)
        refused = False
    except protected.NotTheFounder:
        refused = True
    finally:
        os.environ.pop("OCP_AGENT", None)
    check("bang: an agent's session is refused", refused)


def test_a_shell_output_file_has_no_escape_codes_and_the_folder_keeps_the_newest() -> None:
    from handline.infra import paths
    _home("bangfile")
    with tempfile.TemporaryDirectory() as d:
        folder = paths.home() / "shell"
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(actions.shell.KEEP_FILES + 7):
            f = folder / f"old{i:03}.txt"
            f.write_text("x")
            os.utime(f, (1000 + i, 1000 + i))
        run = actions.ShellRun(r"printf 'a\033[31mred\033[0m\033]0;title\007\nb\rc\n'", d)
        for _ in range(100):
            if not run.running:
                break
            time.sleep(0.05)
        saved = run.save_output()
        text = saved.read_text()
        check("bang file: escape codes and a bare return are not in the file", "\x1b" not in text and "\r" not in text
              and "\x07" not in text and text.startswith("ared"), repr(text))
        left = sorted(f.name for f in folder.glob("*.txt"))
        check("bang file: one save leaves KEEP_FILES files, the newest of them",
              len(left) == actions.shell.KEEP_FILES and saved.name in left and "old000.txt" not in left and "old007.txt" not in left
              and "old008.txt" in left, str(len(left)))


def test_a_card_mounted_on_a_finished_run_sets_its_timer_first_and_sends_once() -> None:
    from handline.ui.components.shell_card import ShellCard
    _home("bangdone")
    with tempfile.TemporaryDirectory() as d:
        run = actions.ShellRun("echo hi", d)
        for _ in range(100):
            if not run.running:
                break
            time.sleep(0.05)
        card = ShellCard(run)
        posted, timers = [], []

        class Timer:
            def stop(self) -> None:
                timers.append("stopped")
        card.set_interval = lambda *a: Timer()
        card.post_message = posted.append
        card.query_one = lambda *a: type("S", (), {"scroll_end": lambda self, **k: None})()
        for w in (card._head, card._out, card._cut):
            w.update = lambda *a: None
        try:
            card.on_mount()
            ok = True
        except AttributeError as e:
            ok = False
            print(e)
        check("shell card: mounting on a finished run does not use its timer before it is set", ok)
        check("shell card: a finished run is sent on its own, once",
              len(posted) == 1 and isinstance(posted[0], ShellCard.Send), str(posted))
        check("shell card: it is marked sent when posted, before the page handles it", run.sent)
        card._final = False
        card.show()
        check("shell card: a run already sent is not sent again", len(posted) == 1)


async def test_a_bang_line_in_the_chat_box_shows_a_card_and_sends_nothing() -> None:
    from handline.ui.components.shell_card import ShellCard
    from textual.widgets import Button
    _home("bangcard")

    async def until(pilot, cond, seconds: float = 15.0) -> None:
        """Wait for `cond` itself, polling, instead of a sleep a loaded machine can overrun."""
        for _ in range(int(seconds / 0.05)):
            if cond():
                return
            await pilot.pause(0.05)
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause(0.5)
        page = app.screen
        check("bang card: the chat page is open", isinstance(page, ChatPage), type(page).__name__)
        con = db.connect()
        before = con.execute("SELECT count(*) FROM requests").fetchone()[0]
        con.close()
        box = page.query_one("#compose")
        box.text = "!echo hello; exit 2"
        await pilot.press("enter")
        await until(pilot, lambda: len(list(page.query(ShellCard))) == 1)
        cards = list(page.query(ShellCard))
        await until(pilot, lambda: cards[0]._send.display and not cards[0]._stop.display)   # its own redraw
        check("bang card: one card was drawn and the box is empty", len(cards) == 1 and box.text == "", f"{len(cards)} {box.text!r}")
        text = str(cards[0]._head.render()) + str(cards[0]._out.render())
        check("bang card: it shows the command, the exit code and the output",
              "echo hello" in text and "exit 2" in text and "hello" in text, text)
        def posted() -> list:
            con = db.connect()
            try:
                return con.execute("SELECT body FROM requests").fetchall()
            finally:
                con.close()
        await until(pilot, lambda: len(posted()) > before and page.shells[0].sent and not page.sending)
        rows = posted()
        check("bang card: the finished command went to the agent on its own, as a file path and not the output",
              len(rows) == before + 1 and "echo hello" in rows[-1][0] and "/shell/" in rows[-1][0], str(rows))
        check("bang card: a finished command offers Send to agent, not Stop",
              cards[0]._send.display and not cards[0]._stop.display)
        box.text = "!sleep 30"
        await pilot.press("enter")
        await until(pilot, lambda: len(page.shells) == 2)
        run = page.shells[-1]
        check("bang card: a running command is running", run.running)
        await pilot.press("escape")
        await until(pilot, lambda: not run.running)
        await pilot.pause(0.2)
        check("bang card: Esc stops it and stays on the page", not run.running and app.screen is page, str(run.code))
        Path(os.environ.get("BANG_SHOT", os.devnull)).write_text(app.export_screenshot())


def test_for_you_is_read_away_a_line_or_all_but_what_is_owed_an_answer_stays() -> None:
    from handline.team import ask as askmod
    _home("foryouread")
    agents.register("reviewer")
    agents.register("backend")
    askmod.say("human", "report one", requester="reviewer")
    askmod.say("human", "report two", requester="reviewer")
    askmod.say("human", "fyi", requester="backend")
    askmod.ask("human", "decide this please", requester="backend")
    con = db.connect()
    try:
        keys = [m["key"] for m in actions.messages(con)]
        done, kept = actions.mark_read(con, actions.fold_key("reviewer"))
        left = [m["key"] for m in actions.messages(con)]
        check("for you read: reading a line reads the whole line away", (done, kept) == (2, 0) and actions.fold_key("reviewer") not in left,
              str((keys, done, kept, left)))
        done, kept = actions.mark_read(con)
        rows = actions.messages(con)
        check("for you read: reading all reads the rest but a reply owed stays, and says so",
              done == 1 and kept == 1 and len(rows) == 1 and "decide" in rows[0]["body"], str((done, kept, rows)))
        check("for you read: a row that is owed an answer cannot be read away", actions.mark_read(con, rows[0]["key"]) == (0, 0))
    finally:
        con.close()
    con = db.connect()
    try:
        check("for you read: and it stays read in a fresh connection", len(actions.messages(con)) == 1)
    finally:
        con.close()


async def test_m_and_M_on_the_for_you_panel_read_a_line_and_all() -> None:
    from handline.team import ask as askmod
    _home("foryoukeys")
    agents.register("reviewer")
    agents.register("backend")
    askmod.say("human", "report one", requester="reviewer")
    askmod.say("human", "fyi", requester="backend")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        page = app.screen
        page.panel = tui.MESSAGES
        page.sel[tui.MESSAGES] = actions.fold_key("reviewer")
        page._redraw()
        await pilot.pause(0.5)
        await pilot.press("m")
        await pilot.pause(0.8)
        con = db.connect()
        left = [m["key"] for m in actions.messages(con)]
        con.close()
        check("for you keys: m read the line under the cursor", left == [actions.fold_key("backend")], str(left))
        page.sel[tui.MESSAGES] = actions.fold_key("backend")
        await pilot.press("M")
        await pilot.pause(0.8)
        con = db.connect()
        left = actions.messages(con)
        con.close()
        check("for you keys: M read the rest", left == [], str(left))
        Path(os.environ.get("FORYOU_SHOT", os.devnull)).write_text(app.export_screenshot())


def test_an_agents_note_on_its_own_row_does_not_repeat_its_name() -> None:
    from handline.ui.components.agent_rows import agent_note
    check("note: a terminal that is gone says just that",
          agent_note("legacy.clearlogo", "legacy.clearlogo's terminal is gone - Orca no longer has it") == "terminal is gone")
    check("note: a name inside the sentence becomes it, and only a whole name",
          agent_note("a", "no session is running in a's terminal") == "no session is running in its terminal"
          and agent_note("a", "a is closed and b is at 98%") == "closed and b is at 98%")


async def _run_all() -> None:
    test_the_stall_watchdog_writes_the_stacks_and_the_recovery()
    await test_changing_owners_on_the_task_board_does_not_hold_the_screen()
    await test_the_home_redraw_reads_on_a_worker_and_the_loop_only_paints()
    await test_assigning_from_a_tasks_page_hands_off_on_a_thread()
    await test_switching_tasks_blanks_the_page_until_the_new_one_is_read()
    await test_a_second_assign_answered_before_the_first_worker_starts_is_refused()
    await test_the_task_board_and_a_task_page_read_on_a_worker_after_an_action()
    await test_the_schedules_are_reachable_on_the_agents_settings_page()
    await test_the_bridge_asks_once_before_restarting_on_new_code()
    test_a_second_bridge_says_so_and_offers_to_take_over()
    test_run_execs_the_same_entrypoint_only_when_a_restart_is_owed()
    test_every_box_over_the_page_has_a_close_button_and_escape_closes_it()
    test_a_code_check_that_began_before_not_now_does_not_ask_about_the_declined_version()
    await test_the_stall_log_never_holds_what_is_typed_and_the_pid_file_is_held_by_the_bridge()
    test_dashboard_accounts_table_is_fed_by_accounts_data()
    test_dashboard_agents_table_is_fed_by_agents_data()
    test_agents_data_shows_its_current_task_and_flags_it_ready()
    test_dashboard_messages_table_is_fed_by_messages_data()
    test_a_dialog_on_an_agent_terminal_is_read_and_pressed()
    test_only_y_n_or_escape_answers_a_confirm()
    test_the_menu_offers_what_the_text_could_become()
    test_the_task_pages_write_a_task_as_T_and_a_request_as_a_bare_number()
    test_the_typed_history_keeps_the_newest_and_never_repeats()
    test_the_chat_is_paged_and_hides_what_never_reached_the_agent()
    test_the_chat_page_reads_a_tasks_state_only_for_a_task_delivery()
    test_the_task_list_takes_only_its_own_dropdowns_for_a_sort_or_a_view()
    test_background_work_alone_is_not_working()
    test_the_chat_numbers_an_answer_by_its_own_row()
    test_the_founders_reply_to_a_waiting_message_draws_its_link()
    test_the_founders_reply_to_a_report_draws_its_link()
    test_a_reply_closes_a_waiting_question_or_starts_a_new_request()
    test_a_message_is_one_text_with_its_parts_in_order()
    test_a_message_number_finds_its_chat_and_its_page()
    test_an_hr_card_is_drawn_only_in_the_chats_it_concerns()
    test_task_changes_are_set_among_the_messages_at_their_time()
    test_a_timeline_entry_shows_its_kind_after_the_time_with_an_icon_and_a_colour_of_its_own()
    test_a_change_names_who_made_it_and_an_assignment_says_to_whom()
    test_a_delivered_task_becomes_doing_when_it_landed_with_no_start_event()
    test_a_reply_shows_a_link_to_what_it_answers()
    test_a_dismissed_request_leaves_no_dismissed_line_in_the_chat()
    test_the_queue_reason_comes_from_the_message_below_the_line()
    test_a_message_between_two_other_agents_can_be_picked_in_all_chat()
    test_a_number_in_a_message_becomes_a_link_to_it()
    test_a_file_path_in_a_message_becomes_a_link_that_opens_it()
    test_a_task_ref_in_a_message_is_found_only_when_it_stands_alone()
    await test_clicking_a_task_ref_in_a_message_opens_that_task_and_a_missing_one_is_refused()
    test_the_task_title_is_cut_to_the_room_beside_the_other_columns()
    test_a_verb_that_only_writes_the_record_runs_on_a_thread_not_a_process()
    test_the_tasks_of_an_agent_are_what_it_owns_or_opened()
    test_a_delivery_changes_the_chat_signature()
    test_batch_results_are_one_toast_for_what_worked_and_one_for_the_refusals()
    test_the_task_page_offers_discard_only_on_the_founders_waiting_task()
    test_a_draft_is_marked_done_by_whoever_may_mark_it_done_and_the_event_says_it_was_a_draft()
    test_the_settings_form_checks_and_saves_only_what_changed()
    test_the_browser_setting_writes_chrome_and_the_sites_through_register()
    await test_the_settings_page_shows_the_browser_mode_and_enables_sites_only_for_playwright()
    await test_the_hr_tab_edits_any_agents_settings_and_shows_a_settings_proposal()
    test_a_bang_line_runs_in_zsh_in_the_worktree_and_an_agent_cannot_run_one()
    await test_a_bang_line_in_the_chat_box_shows_a_card_and_sends_nothing()
    test_for_you_is_read_away_a_line_or_all_but_what_is_owed_an_answer_stays()
    test_an_agents_note_on_its_own_row_does_not_repeat_its_name()
    await test_m_and_M_on_the_for_you_panel_read_a_line_and_all()
    await test_the_task_page_queues_a_draft_and_drafts_a_queued_task()
    test_a_message_head_tells_its_kind_and_linked_tasks()
    test_a_handoff_is_one_card_not_a_changed_task_and_a_new_task()
    await test_the_chat_page_draws_a_handoff_as_one_card()
    test_what_an_agent_wrote_is_drawn_inert_on_every_part_of_the_page()
    test_key_guide_keeps_the_high_ranks_on_one_line_and_ends_with_a_question_mark()
    test_key_guide_never_keeps_a_lower_rank_while_a_higher_one_is_left_out()
    test_the_weekly_outlook_averages_accounts_and_restores_at_reset()
    test_an_account_that_ends_steps_the_forecast_down_by_its_share()
    test_the_overlay_draws_what_the_agent_ate_in_front_of_the_headroom_and_shares_add_up()
    test_the_agents_table_is_a_tree_with_the_group_total_on_top()
    test_the_outlook_chart_draws_a_column_per_slot_with_the_guess_apart()
    test_a_not_sent_or_not_cancelled_reply_is_an_error_toast()
    test_the_status_line_under_the_box_leaves_out_what_the_agent_waits_on()
    test_a_send_that_went_shows_no_toast_and_a_refused_one_does()
    test_gauge_elapsed_fraction_bar_and_usage_cells()
    test_agent_rows_short_model_cache_and_founder_block()
    test_agent_rows_collect_carries_the_agents_own_model_and_effort()
    test_fold_urgency_ranks_what_a_fold_hides()
    test_agent_rows_collect_says_why_a_row_needs_the_founder()
    test_agent_rows_collect_keeps_a_stuck_reason_whole()
    test_open_proposals_lists_only_what_is_the_founders_move()
    test_summary_line()
    test_idle_close_is_set_globally_and_per_agent_in_minutes()
    test_global_switches_read_and_write_the_keys_the_daemon_reads()
    await test_settings_save_at_once_and_going_back_asks_only_about_an_unsaved_value()
    await test_a_tasks_timeout_is_shown_on_the_board_and_the_task_page()
    test_a_standing_answer_is_added_from_the_settings()
    test_home_settings_removal_and_the_delegated_view()
    test_esc_on_a_block_modal_is_a_cancel_sent_to_the_daemon()
    test_the_home_cursor_moves_only_when_the_row_is_not_already_there()
    test_the_bridge_stylesheet_parses()
    test_the_open_task_counts_read_once_and_match_the_one_agent_count()
    test_agents_data_scans_the_open_tasks_once_for_all_agents()
    test_schedules_are_built_read_and_written_through_actions()
    test_assign_offers_recent_targets_then_the_holders_team()
    test_a_timeline_entry_that_fits_stays_one_line_and_a_longer_one_shows_whole()
    test_assign_answers_from_the_task_page()
    test_a_task_page_shows_every_line_of_a_multi_line_task()
    await test_the_shared_task_view_draws_the_brief()
    test_a_task_timeline_holds_memos_and_handoffs_in_time_order()
    test_a_slash_line_is_a_bridge_command_and_never_a_message()
    await test_restart_is_a_chat_command_with_a_fresh_form()
    test_the_permission_modal_shows_the_whole_panel_without_the_tip()
    test_key_token_translates_textual_keys()
    test_the_agent_row_names_the_fold_alarm_and_why_it_is_stuck()
    test_a_message_sits_where_the_agent_read_it_and_a_line_ahead_of_the_queue()
    test_the_chat_says_typed_until_the_transcript_shows_the_line()
    test_the_context_setting_is_in_thousands_and_shows_its_share_of_the_window()
    test_a_queued_message_says_why_it_has_not_reached_the_agent()
    test_a_blocked_heads_queue_reason_names_the_block_not_a_reversed_behind()
    test_an_agents_queued_message_waits_below_the_line_without_buttons()
    await test_a_change_of_the_agents_state_redraws_the_spinner_not_the_chat()
    await test_neither_tasks_tab_scrolls_sideways()
    await test_the_chat_page_draws_a_changed_task_as_a_one_line_card()
    await test_confirm_ask_runs_its_action_only_on_yes_and_from_the_app()
    await test_going_home_from_a_chat_with_a_draft_leaves_home_answering_keys()
    await test_a_chat_clears_its_agents_for_you_and_opens_on_an_old_message()
    await test_a_question_whose_press_raises_still_closes_with_the_reason()
    await test_the_schedule_form_builds_the_cron_and_the_list_removes_the_row_pressed()
    await test_the_task_page_edits_the_task_text_and_shows_a_refusal()
    await test_an_open_task_page_shows_what_an_agent_adds_without_reopening()
    await test_the_task_page_sends_a_draft_straight_to_done_from_its_button_and_its_key()
    test_every_timeline_kind_the_task_page_can_get_is_drawn()
    await test_a_blocked_task_says_so_where_working_would_be()
    await test_the_task_timeline_shows_a_long_entry_whole_beside_its_icons()
    await test_a_long_timeline_scrolls_to_its_last_entry_and_shows_every_line_of_each()
    await test_the_memo_box_asks_whoever_is_picked_and_the_chat_does_not_echo_it()
    await test_every_dialog_opens_on_its_field_and_leaves_once()
    await test_a_session_start_is_a_quiet_system_line_and_its_handoff_opens_in_a_modal()
    await test_an_hr_request_is_a_card_the_founder_answers_in_the_chat_and_on_the_hr_page()
    await test_the_chat_strip_says_the_agents_model_account_context_and_cache_time_left()
    test_the_sandbox_banner_reads_the_lease_and_one_click_ends_it()
    await test_a_scrolled_up_chat_says_how_many_messages_came_and_jumps_to_the_newest()
    await test_older_messages_loaded_above_are_not_counted_as_new()
    await test_answering_a_message_shows_its_whole_body()
    await test_a_and_e_on_a_home_message_open_the_answer_with_its_whole_body()
    await test_the_answer_box_fits_a_short_terminal_and_pages_the_message()
    await test_assign_enter_sends_the_note_whole()
    await test_a_changed_timeline_entry_remounts_only_itself()
    test_account_settings_alias_and_default_cap()
    await test_a_drag_on_a_picked_message_copies_what_was_dragged_and_does_not_unpick_it()
    await test_a_widget_that_cannot_read_its_selection_copies_nothing_and_the_app_lives()
    test_a_late_answer_still_counts_as_new_everywhere()
    test_a_shell_output_file_has_no_escape_codes_and_the_folder_keeps_the_newest()
    test_a_card_mounted_on_a_finished_run_sets_its_timer_first_and_sends_once()


def main() -> int:
    if "-k" in sys.argv[1:]:        # its tests run in one fixed sequence on one app loop: a filter would run them all
        print("test_bridge_textual.py has no -k filter; it runs whole", file=sys.stderr)
        return 2
    os.environ.pop("OCP_AGENT", None)
    asyncio.run(_run_all())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

