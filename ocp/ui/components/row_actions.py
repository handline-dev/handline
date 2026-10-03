"""What a keypress on a row of the home page does: open its chat, or run the verb the key names
through `jobs.Jobs` (ACCOUNTS' login and add hand the terminal to another program instead)."""
from __future__ import annotations

from ...team import accounts
from ... import actions, db
from ...session import orca
from .. import tui
from .account_actions import AccountActions
from .text_prompt import TextPrompt
from .jobs_aware import dispatch_agent_verb


class RowActions(AccountActions):
    """Mixin for a screen that keeps `panel` and `sel` and starts jobs (`JobsAware`)."""

    def open_chat(self, agent: str, focus: tuple[int, int] | None = None) -> None:
        """`focus` is a (request id, part) the chat opens with picked as the message to reply to."""
        from ..chat_page import ChatPage  # chat_page imports the components this page uses
        self.app.push_screen(ChatPage(agent, focus))

    async def _act_and_redraw(self, key: str) -> None:
        await self._act(key)
        self._redraw()

    # ---------------------------------------------------------- row actions

    async def _act(self, key: str) -> None:
        """One keypress on the selected row - `tui._act_on`'s decisions
        (which key does what, what asks first, what needs a prompt), `jobs.Jobs`
        for running it (or, for ACCOUNTS' login/add, `App.suspend()`: those
        hand the whole terminal to another program)."""
        panel, sel = self.panel, self.sel
        if panel == tui.ACCOUNTS and key == "a":
            # Adding needs no row under the cursor - the first account is
            # added to an empty panel, so this comes before the "no rows" guard.
            await self._account_add()
            return
        if panel == tui.MESSAGES and key == "M":
            await self._read_for_you(None)
            return
        keys = tui._keys_of(panel)
        target = sel.get(panel)
        if not keys or target not in keys:
            return
        con = db.connect()
        try:
            if panel == tui.ACCOUNTS:
                await self._act_account(con, str(target), key)
            elif panel in (tui.AGENTS, tui.STUCK):
                await self._act_agent(con, str(target), key)
            elif panel == tui.MESSAGES:
                await self._act_message(con, target, key)
        finally:
            con.close()

    async def _act_account(self, con, name: str, key: str) -> None:
        if key in ("\r", "\n"):
            from ..pages.account import AccountPage   # the page imports these components
            self.app.push_screen(AccountPage(name))
            return
        if key == "l":
            await self._account_login(name)
            return
        if key == "c":
            await self._account_cap(name)
            return
        if key == "x":
            await self._account_remove(name)
            return

    async def _account_add(self) -> None:
        name = await self.app.push_screen_wait(TextPrompt("new account name:"))
        if not name or not name.strip():
            self.toast("not added")
            return
        name = name.strip()
        ok, msg = accounts.add_account(name, lambda argv, env: self._run_suspended(argv, env)[0])
        self.toast(msg, ok)
        if not ok or not orca.available():
            return
        # 156 item 1c: the CLI and this screen share the one trusting sequence.
        declared, skipped = actions.eligible_worktrees()
        if not declared:
            return
        listed = ", ".join(f"{a} ({w})" for a, w in declared)
        prompt = f"trust {listed}? y/N" + (f" (skipping {len(skipped)} outside declared roots)"
                                           if skipped else "")
        yn = await self.app.push_screen_wait(TextPrompt(prompt))
        if (yn or "").strip().lower() in ("y", "yes"):
            ok, said = actions.trust_worktrees(name, declared)
            self.toast(msg + " · " + said, ok)

    async def _act_agent(self, con, name: str, key: str) -> None:
        if key in ("\r", "\n"):    # "↵" in AGENT_KEYS is a display label, never a lookup key
            self.open_chat(name)
            return
        if key == "?":
            # The footer (`tui.panel_keys`) folds fold/move out of a filtered
            # row's own keys - `?` is the one place every AGENT_KEYS verb is named.
            self.toast(tui.agent_help_line())
            return
        label = dict(tui.AGENT_KEYS).get(key)
        if not label or label == "open":
            return
        await dispatch_agent_verb(self, con, name, label)

    async def _read_for_you(self, key) -> None:
        """m on a line, M on all of them (`key` None): read away from FOR YOU what is not owed an answer."""
        con = db.connect()
        try:
            done, kept = actions.mark_read(con, key)
        finally:
            con.close()
        if not done:
            self.toast("nothing to read away: these wait on you", False)
        else:
            self.toast(f"marked {done} read" + (f"; {kept} waiting on you stay" if kept else ""))

    async def _act_message(self, con, target, key: str) -> None:
        # `target` is the row's stable key, not its id: a folded row's id is
        # its latest thing, which can have changed since the row was
        # selected, so it is looked up fresh rather than trusted from then
        # (the row's id is what the action is given).
        row_m = next((m for m in actions.messages(con) if m["key"] == target), None)
        if row_m is None:
            self.toast(f"{target} is gone", False)
            return
        rid = row_m["id"]
        row = con.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()
        if not row:
            self.toast(f"#{rid} is gone", False)
            return
        if key in ("\r", "\n"):
            actions.opened_by_human(con, rid)
            place = actions.message_place(con, rid)
            if place is None:
                self.toast(f"#{rid} is gone", False)
                return
            self.open_chat(place[0], (rid, place[1]))
            return
        if key == "m":
            await self._read_for_you(target)
            return
        if key == "x":
            self.toast(self._start("dismiss", f"#{rid}", f"dismissing #{rid}", (rid,),
                                   {"me": actions.HUMAN}), False)
            return
        if key in ("a", "e"):
            text = await self.app.push_screen_wait(TextPrompt(
                f"answer #{rid}:", multi=key == "e", context=row["body"] or ""))
            if not text:
                self.toast("nothing sent: a or e again to retry")
                return
            self.toast(self._start("answer", f"#{rid}", f"answering #{rid}", (rid, text)), False)
