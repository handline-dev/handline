"""opencode, measured on 1.18.32 (design/03 §4.3)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

# `opencode auth list`'s closing line, "<n> credentials", whether or not it is
# given a terminal.
_CREDENTIALS = re.compile(r"\b(\d+) credentials\b")
# The executable, at the start of the command line, followed by an option or
# by nothing: the TUI. A subcommand word in that place (`run`, `serve`,
# `auth`, ...) is a headless or one-shot run.
_SESSION = re.compile(r"^(?:\S*/)?opencode(?:\s+-|$)")
# ocp's disallowed tools, by claude's names, as opencode's permission keys.
# Only Agent has a counterpart: opencode has no SendMessage, ScheduleWakeup
# or CronCreate to deny.
_TOOLS = {"Agent": "task"}
# The bottom edge of the prompt box; the rows under it are the footer.
_BOX_BOTTOM = re.compile(r"^\s*\u2579\u2580{8,}")
# How far above the last row the box's bottom edge may sit: two footer rows
# in a session, four on the start screen.
_FOOTER_ROWS = 6



class Opencode:
    name = "opencode"
    min_version = (1, 18, 32)
    # XDG_DATA_HOME, so opencode keeps everything of this account's under
    # `<this>/opencode/`: auth.json, the session db, the logs.
    config_dir_name = "xdg-data"

    def binary(self) -> str | None:
        return shutil.which("opencode")

    def config_dir_env(self) -> str:
        """The only variable that moves the credential. OPENCODE_CONFIG_DIR
        adds a config dir and moves nothing; config and state stay shared."""
        return "XDG_DATA_HOME"

    def is_session(self, command: str) -> bool:
        """The command line starts with the binary and goes on with an option,
        or ends there. Anchored to the start because a claude session's
        preamble can say "opencode" anywhere in its own command line."""
        return _SESSION.match(command) is not None

    def meters(self) -> bool:
        # It stores what was spent, never how much of a limit is left.
        return False

    def is_resume(self, extra: list[str] | None) -> bool:
        return any(x in ("-s", "--session", "-c", "--continue") or x.startswith("--session=")
                   for x in (extra or []))

    def launch_argv(self, exe: str, model: str | None, effort: str, disallowed: str,
                    mcp: list[str], preamble: str | None) -> list[str]:
        """The TUI takes the model, as `provider/model`, and nothing else ocp
        passes: it has no flag for effort (only `run --variant` does), tool
        denial, an MCP config or a system prompt. Those reach opencode through
        its config, not its argv."""
        argv = [exe]
        if model:
            argv += ["-m", str(model)]
        return argv

    def launch_env(self, preamble: str | None, disallowed: str, state_dir: Path) -> dict[str, str]:
        """A config of its own for this session, in OPENCODE_CONFIG_CONTENT,
        merged over the shared one. The preamble goes in as an instructions
        file, added to opencode's system prompt and read again for every
        session in the process, a /new's included. A denied tool is taken out
        of the model's tool list, not refused when called."""
        cfg: dict = {}
        if preamble:
            state_dir.mkdir(parents=True, exist_ok=True)
            f = state_dir / "opencode-preamble.md"
            f.write_text(preamble)
            cfg["instructions"] = [str(f)]
        deny = {_TOOLS[t]: "deny" for t in disallowed.split(",") if t in _TOOLS}
        if deny:
            cfg["permission"] = deny
        return {"OPENCODE_CONFIG_CONTENT": json.dumps(cfg)}

    def prompt_argv(self, prompt: str) -> list[str]:
        # A positional is the project directory; --prompt submits on start.
        return ["--prompt", prompt]

    def clear_command(self) -> str:
        return "/new"

    def exit_command(self) -> str:
        return "/exit"

    idle_from_screen = True

    def _footer(self, tail: list[str]) -> list[str] | None:
        """The rows under the prompt box, or None when no box is drawn."""
        rows = [str(x) for x in tail[-_FOOTER_ROWS:]]
        edge = next((i for i in range(len(rows) - 1, -1, -1) if _BOX_BOTTOM.match(rows[i])), None)
        return None if edge is None else rows[edge + 1:]

    def idle_on_screen(self, tail: list[str]) -> bool:
        """The prompt box is drawn and its footer offers no interrupt: a turn
        in progress puts "esc interrupt" there, in place of the directory and
        "ctrl+p commands"."""
        footer = self._footer(tail)
        return footer is not None and not any("interrupt" in r for r in footer)

    def screen_blocker(self, tail: list[str]):
        """A permission request replaces the prompt box with its choices, the
        first already selected, so Enter answers it. Anything without the box
        cannot be typed into safely."""
        from .. import orca, textsafe  # late: orca looks drivers up
        rows = [str(x) for x in tail]
        low = max(0, len(rows) - _FOOTER_ROWS)
        ask = next((i for i in range(len(rows) - 1, low - 1, -1)
                    if "enter confirm" in rows[i]), None)
        if ask is not None:
            lines = [textsafe.oneline(x.strip(" \u2503")) for x in rows[:ask]]
            title = next((i for i in range(len(lines) - 1, -1, -1)
                          if lines[i].startswith("\u25b3")), None)
            shown = [ln for ln in (lines[title:] if title is not None else lines[-3:]) if ln]
            return orca.Blocker(orca.MODAL, " / ".join(shown[:3])[:orca.DETAIL_CHARS])
        if self._footer(tail) is None:
            return orca.Blocker(orca.UNREADABLE)
        return None

    def credential_present(self, config_dir: Path) -> bool:
        """Asked of `opencode auth list`, which counts the credentials under
        this data dir without printing them. The file alone does not answer:
        a logout leaves auth.json behind, holding `{}`."""
        if not (config_dir / "opencode" / "auth.json").exists():
            return False
        exe = self.binary()
        if not exe:
            return False
        try:
            r = subprocess.run([exe, "auth", "list"],
                               env={**os.environ, self.config_dir_env(): str(config_dir)},
                               stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=20)
        except (OSError, subprocess.SubprocessError):
            return False
        m = _CREDENTIALS.search(r.stdout)
        return bool(m) and int(m.group(1)) > 0

    def login_argv(self) -> list[str]:
        # The human picks the provider; for an API-key provider the login is
        # pasting the key.
        return ["opencode", "auth", "login"]

    def logout_argv(self) -> list[str]:
        # With no provider named it asks which one.
        return ["opencode", "auth", "logout"]

    def identity(self, env: dict) -> str | None:
        # A credential is per provider and names no account.
        return None

    def subscription_type(self, env: dict) -> str | None:
        return None

    def shared_items(self) -> list[str]:
        # Config is not under XDG_DATA_HOME, so every account already shares it.
        return []

    def never_touch(self) -> set[str]:
        return {"opencode"}

    def memory_root(self, config_dir: Path) -> Path | None:
        # No per-repo memory, and no transcript files: sessions are rows in
        # the db.
        return None

    def memory_project_dir(self, repo: str) -> str:
        # Never joined to a root, since there is none.
        return repo
