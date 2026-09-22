"""Claude Code: what ocp did before there was a driver, moved here unchanged."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def keychain_service(config_dir: Path) -> str:
    """The macOS Keychain service name Claude Code uses for this config dir.

    Credentials are not a file on macOS. Claude Code keys them per config dir:
    the default ~/.claude uses "Claude Code-credentials", and any other dir gets
    that name plus the first 8 hex of the sha256 of its path. Verified against a
    live login: ~/.orcapipe/accounts/alpha/.claude -> ...-c4b0821c.
    """
    if config_dir == Path.home() / ".claude":
        return "Claude Code-credentials"
    digest = hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]
    return f"Claude Code-credentials-{digest}"


class Claude:
    name = "claude"
    # Nothing is read from the status line any more (D11), so this is no longer
    # about its payload. It stays because it is the floor everything here was
    # measured against: the usage endpoint answers the `claude-code/2.1.0` client
    # `usage_api` presents itself as, and the transcript shape `collect` reads was
    # measured on this version.
    min_version = (2, 1, 0)

    def binary(self) -> str | None:
        return shutil.which("claude")

    def config_dir_env(self) -> str:
        return "CLAUDE_CONFIG_DIR"

    def is_resume(self, extra: list[str] | None) -> bool:
        """Three spellings, because reading `--resume=<id>` as "not a resume" picks an
        account by headroom and then hands claude a session id that exists under
        the other one - a silently fresh session where a resume was asked for.
        """
        return any(x == "--resume" or x.startswith("--resume=") or x == "-r"
                   for x in (extra or []))

    def launch_argv(self, exe: str, model: str | None, effort: str, disallowed: str,
                    mcp: list[str], preamble: str | None) -> list[str]:
        argv = [exe]
        if model:
            argv += ["--model", str(model)]
        argv += ["--disallowedTools", disallowed]
        argv += ["--effort", effort]
        argv += mcp
        if preamble:
            argv += ["--append-system-prompt", preamble]
        return argv

    def credential_present(self, config_dir: Path) -> bool:
        """Never reads the secret: `security find-generic-password` is called without
        -w, so it reports the item's presence and nothing else.
        """
        # File-backed storage (Linux, and macOS installs that opt out of Keychain).
        if (config_dir / ".credentials.json").exists():
            return True
        if sys.platform != "darwin":
            return False
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", keychain_service(config_dir)],
                capture_output=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0

    def login_argv(self) -> list[str]:
        return ["claude", "/login"]

    def logout_argv(self) -> list[str]:
        return ["claude", "auth", "logout"]

    def identity(self, env: dict) -> str | None:
        """Asked of `claude auth status`, which answers from its own records - ocp
        reads no credential and no .claude.json to learn it (KICKOFF rule 1).
        """
        claude = self.binary()
        if not claude:
            return None
        try:
            r = subprocess.run([claude, "auth", "status"], env=env,
                               capture_output=True, text=True, timeout=30)
            return json.loads(r.stdout).get("email") or None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def subscription_type(self, env: dict) -> str | None:
        """`claude auth status --json` is claude's own record of what it is signed
        in as; it touches no credential file."""
        try:
            out = subprocess.run(
                ["claude", "auth", "status", "--json"],
                env=env, capture_output=True, text=True, timeout=10,
            ).stdout
            data = json.loads(out)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
            return None
        sub = data.get("subscriptionType") if isinstance(data, dict) else None
        return sub if isinstance(sub, str) and sub else None

    def shared_items(self) -> list[str]:
        # Nothing credential-shaped appears here, deliberately.
        return ["settings.json", "CLAUDE.md", "skills", "agents", "commands", "plugins"]

    def never_touch(self) -> set[str]:
        # Named explicitly as a guard: the credential file the installs that do
        # not use the Keychain keep (KICKOFF rule 1), claude's own config, and
        # its machine state.
        return {".credentials.json", ".claude.json", "statsig", "backups"}

    def memory_root(self, config_dir: Path) -> Path:
        return config_dir / "projects"

    def memory_project_dir(self, repo: str) -> str:
        """Claude Code's own path encoding: every non-alphanumeric character
        mapped to '-'."""
        return re.sub(r"[^A-Za-z0-9]", "-", repo)
