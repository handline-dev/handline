"""What the account keys do - log in, set the cap, remove - for the home page's ACCOUNTS
row and for the account page alike. A screen using it keeps a `flash`."""
from __future__ import annotations

import subprocess

from ...team import accounts
from ... import actions, db
from ...usage import load as loadmod
from .text_prompt import TextPrompt


class AccountActions:
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
        self.flash = actions.set_account_cap(name, text)[1]

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
