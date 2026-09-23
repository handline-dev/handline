"""opencode, measured on 1.18.32 (design/03 §4.3)."""
from __future__ import annotations

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
