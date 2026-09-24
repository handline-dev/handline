"""What a keypress on a row of the home page does: open its chat, or run the verb the key names
through `jobs.Jobs` (ACCOUNTS' login and add hand the terminal to another program instead)."""
from __future__ import annotations

import subprocess

from ... import accounts, actions, db, load as loadmod, orca, tui
from .text_prompt import TextPrompt
from .jobs_aware import dispatch_agent_verb
from .read_only import ReadOnly


class RowActions:
    """Mixin for a screen that keeps `panel`, `sel` and `flash` and starts jobs (`JobsAware`)."""

    def open_chat(self, agent: str) -> None:
        from ...chat_page import ChatPage   # chat_page imports the components this page uses
        self.app.push_screen(ChatPage(agent))

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
            sections = actions.account_detail(con, name, accounts.logged_in)
            await self.app.push_screen_wait(
                ReadOnly(f"account {name}", sections))
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

    def _run_suspended(self, argv: list[str], env: dict) -> tuple[int | None, str]:
        """`tui._suspend`'s own contract, through `App.suspend()`: hand the
        whole terminal to `argv` (a login is a human typing a code into
        claude's own screen - nothing about that can happen behind Textual's
        screen), then take it back."""
        try:
            with self.app.suspend():
                r = subprocess.run(argv, env=env)
                return r.returncode, f"{argv[0]} exited {r.returncode}"
        except (OSError, subprocess.SubprocessError) as e:
            return None, f"could not run {argv[0]}: {e}"

    async def _account_add(self) -> None:
        name = await self.app.push_screen_wait(TextPrompt("new account name:"))
        if not name or not name.strip():
            self.flash = "not added"
            return
        name = name.strip()
        ok, msg = accounts.add_account(name, lambda argv, env: self._run_suspended(argv, env)[0])
        self.flash = msg
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
            self.flash = msg + " · " + "; ".join(actions.trust_worktrees(name, declared))

    async def _account_login(self, name: str) -> None:
        line, argv, env = actions.login_argv(name)
        if not argv:
            self.flash = line
            return
        _, self.flash = self._run_suspended(argv, env)

    async def _account_cap(self, name: str) -> None:
        text = await self.app.push_screen_wait(
            TextPrompt(f"most agents mid-turn on {name} at once [{loadmod.cap_of(name)}]:"))
        if not text or not text.strip():
            self.flash = "cap unchanged"
            return
        try:
            loadmod.set_cap(name, int(text.strip()))
        except ValueError:
            self.flash = "a cap is a whole number, at least 1"
            return
        self.flash = f"{name}: at most {text.strip()} agents mid-turn"

    async def _account_remove(self, name: str) -> None:
        # `remove_plan` first, the same order the CLI uses: an
        # account in use is refused before anyone is asked to type its name,
        # not after - `remove_account` checks it again post-confirm, since
        # that wait is on a person and an agent can start in the meantime.
        con = db.connect()
        try:
            ok, why = accounts.remove_plan(name, con)
        finally:
            con.close()
        if not ok:
            self.flash = why
            return
        typed = await self.app.push_screen_wait(
            TextPrompt(f"remove '{name}'? type its name to confirm:"))
        confirmed = typed == name
        con = db.connect()
        try:
            ok, msg = accounts.remove_account(
                name, lambda: confirmed,
                lambda argv, env: self._run_suspended(argv, env)[0], con)
        finally:
            con.close()
        self.flash = msg

    async def _act_agent(self, con, name: str, key: str) -> None:
        if key in ("\r", "\n"):    # "↵" in AGENT_KEYS is a display label, never a lookup key
            self.open_chat(name)
            return
        if key == "?":
            # The footer (`tui.panel_keys`) folds fold/move out of a filtered
            # row's own keys - `?` is the one place every AGENT_KEYS verb is named.
            self.flash = tui.agent_help_line()
            return
        label = dict(tui.AGENT_KEYS).get(key)
        if not label or label == "open":
            return
        await dispatch_agent_verb(self, con, name, label)

    async def _act_message(self, con, target, key: str) -> None:
        # `target` is the row's stable key, not its id: a folded row's id is
        # its latest thing, which can have changed since the row was
        # selected, so it is looked up fresh rather than trusted from then
        # (the row's id is what the action is given).
        row_m = next((m for m in actions.messages(con) if m["key"] == target), None)
        if row_m is None:
            self.flash = f"{target} is gone"
            return
        rid = row_m["id"]
        row = con.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()
        if not row:
            self.flash = f"#{rid} is gone"
            return
        if key in ("\r", "\n"):
            actions.opened_by_human(con, rid)
            place = actions.message_place(con, rid)
            if place is not None:
                self.open_chat(place[0])
                return
            detail = actions.message_detail(con, rid)
            if detail is None:
                self.flash = f"#{rid} is gone"
                return
            title, sections = detail
            await self.app.push_screen_wait(ReadOnly(title, sections))
            return
        if key == "x":
            self.flash = self._start("dismiss", f"#{rid}", f"dismissing #{rid}", (rid,),
                                     {"me": actions.HUMAN})
            return
        if key in ("a", "e"):
            text = await self.app.push_screen_wait(TextPrompt(f"answer #{rid}:", multi=key == "e"))
            if not text:
                self.flash = "nothing sent: a or e again to retry"
                return
            self.flash = self._start("answer", f"#{rid}", f"answering #{rid}", (rid, text))
