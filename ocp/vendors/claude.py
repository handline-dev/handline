"""Claude Code: what ocp did before there was a driver, moved here unchanged."""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..infra.paths import user_home


def keychain_service(config_dir: Path) -> str:
    """The macOS Keychain service name Claude Code uses for this config dir.

    Credentials are not a file on macOS. Claude Code keys them per config dir:
    the default ~/.claude uses "Claude Code-credentials", and any other dir gets
    that name plus the first 8 hex of the sha256 of its path. Verified against a
    live login: ~/.orcapipe/accounts/alpha/.claude -> ...-c4b0821c.
    """
    if config_dir == user_home() / ".claude":
        return "Claude Code-credentials"
    digest = hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]
    return f"Claude Code-credentials-{digest}"


# `profile`: a tier is a short snake_case word ("default_claude_ai",
# "default_claude_max_5x"); anything else in that field is not passed on.
_TIER = re.compile(r"[a-z0-9_]{1,64}")
_PROFILES: dict[Path, tuple[tuple[int, int], dict]] = {}


def _iso_epoch(v) -> float | None:
    """An ISO-8601 timestamp ("2026-09-21T04:43:56.433836Z") as epoch seconds."""
    if not isinstance(v, str) or len(v) > 40:
        return None
    try:
        return datetime.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class Claude:
    name = "claude"
    # Nothing is read from the status line any more (D11), so this is no longer
    # about its payload. It stays because it is the floor everything here was
    # measured against: the usage endpoint answers the `claude-code/2.1.0` client
    # `usage_api` presents itself as, and the transcript shape `collect` reads was
    # measured on this version.
    min_version = (2, 1, 0)
    config_dir_name = ".claude"

    def binary(self) -> str | None:
        return shutil.which("claude")

    def config_dir_env(self) -> str:
        return "CLAUDE_CONFIG_DIR"

    # fold's own cheap default when nobody asked for a model by name - a
    # human's --model always wins; this is only ever what a driver-agnostic
    # caller falls back to for its one, small headless call.
    DEFAULT_HEADLESS_MODEL = "haiku"

    def headless_argv(self, exe: str, model: str | None, prompt: str) -> list[str] | None:
        return [exe, "--model", self.headless_model(model), "-p", prompt]

    def can_headless(self) -> bool:
        return True

    def headless_model(self, model: str | None) -> str:
        return model or self.DEFAULT_HEADLESS_MODEL

    def is_session(self, command: str) -> bool:
        """`claude` in the command line, headless runs excluded. Headless here
        is `-p`, which is claude's spelling and nobody else's."""
        if "claude" not in command:
            return False
        return " -p " not in command and not command.endswith(" -p")

    def meter(self):
        from .claude_meter import ClaudeMeter       # it imports this module
        return ClaudeMeter()

    def collector(self):
        from .claude_collect import ClaudeCollector
        return ClaudeCollector()

    def context_window(self, model_id: str | None) -> int | None:
        from .claude_collect import context_window
        return context_window(model_id)

    def state_hooks(self):
        from .claude_hooks import ClaudeHooks       # it imports this module
        return ClaudeHooks()

    def is_resume(self, extra: list[str] | None) -> bool:
        """Three spellings, because reading `--resume=<id>` as "not a resume" picks an
        account by headroom and then hands claude a session id that exists under
        the other one - a silently fresh session where a resume was asked for.
        """
        return any(x == "--resume" or x.startswith("--resume=") or x == "-r"
                   for x in (extra or []))

    def transcript_dir(self, config_dir: Path, worktree: str) -> Path:
        """Where the sessions run in `worktree` are recorded: claude names the
        folder after the path, every character that is not a letter or digit
        becoming a dash."""
        return config_dir / "projects" / re.sub(r"[^A-Za-z0-9]", "-", worktree)

    def launch_argv(self, exe: str, model: str | None, effort: str, disallowed: str,
                    mcp: list[str], preamble: str | None,
                    hide: list[str] | None = None,
                    memory_dir: Path | None = None,
                    trusted: list[str] | None = None,
                    chrome: bool = False) -> list[str]:
        argv = [exe]
        if model:
            argv += ["--model", str(model)]
        argv += ["--disallowedTools", disallowed]
        argv += ["--effort", effort]
        # --no-chrome (claude --help) keeps the unanswered "Claude wants to
        # use your browser" dialog off every session's start; an agent with
        # `chrome: true` in its agent.yaml (T875) opts in with --chrome.
        argv += ["--chrome" if chrome else "--no-chrome"]
        argv += mcp
        if preamble:
            argv += ["--append-system-prompt", preamble]
        # One --settings object: a second one would replace the first.
        settings = self.memory_settings(memory_dir) if memory_dir else {}
        if hide:
            # denyRead binds Bash; the Read tool is a permission, so deny it there too.
            settings["sandbox"] = {"filesystem": {"denyRead": hide}}
            settings["permissions"] = {"deny": [f"Read(/{p.rstrip('/')}/**)" for p in hide]}
        if trusted:
            # The classifier reads autoMode from --settings, not from the
            # project's settings; "$defaults" keeps the built-in entries.
            settings["autoMode"] = {"environment": ["$defaults", *trusted]}
        if settings:
            argv += ["--settings", json.dumps(settings)]
        return argv

    def memory_settings(self, memory_dir: Path) -> dict:
        return {"autoMemoryDirectory": str(memory_dir)}

    def skip_permissions_argv(self) -> list[str]:
        return ["--dangerously-skip-permissions"]

    def auto_mode_argv(self) -> list[str]:
        # The flag, not permissions.defaultMode: a settings file's "auto" is
        # ignored in a project's settings and unreliable elsewhere.
        return ["--permission-mode", "auto"]

    def launch_env(self, preamble: str | None, disallowed: str, state_dir: Path) -> dict[str, str]:
        return {}

    def prompt_argv(self, prompt: str) -> list[str]:
        return [prompt]

    def clear_command(self) -> str:
        return "/clear"

    def exit_command(self) -> str:
        return "/exit"

    idle_from_screen = False

    def idle_on_screen(self, tail: list[str]) -> bool:
        return True

    def working_on_screen(self, tail: list[str]) -> bool | None:
        # Orca reads claude's working state; the screen carries no turn
        # marker ocp reads, so it is never the witness.
        return None

    def idle_with_background(self, tail: list[str]) -> bool:
        from ..session import orca  # late: orca looks drivers up
        return _idle_with_background(orca, tail)

    def screen_blocker(self, tail: list[str]):
        from ..session import orca  # late: orca looks drivers up
        return _screen_blocker(orca, tail)

    def trust_screen(self, tail: list[str]) -> str | None:
        from ..session import orca  # late: orca looks drivers up
        return _trust_screen(orca, tail)

    def trust_answer(self) -> str:
        return TRUST_PROMPT_DOWN_ARROW

    def accepts_tool(self, tool: str | None) -> bool:
        # launch_argv passes --no-chrome unless the agent opted in (T875), and an
        # opted-in agent's browser prompt is the founder's to answer.
        return False

    def accepting_key(self, dialog, tool: str | None) -> tuple[str | None, str]:
        return None, "this vendor's prompts are not auto-accepted"

    def confirmation(self, dialog):
        from . import claude_confirm
        return claude_confirm.recognize(dialog)

    def ask_question(self, tail: list[str]):
        from . import claude_ask
        return claude_ask.recognize(tail)

    def answer_question_keys(self, question, choices=(), text=None):
        from . import claude_ask
        return claude_ask.question_keys(question, choices, text)

    def question_toggle_keys(self, question, choices):
        from . import claude_ask
        return claude_ask.toggle_keys_for_other(question, choices)

    def question_other_after_toggle_keys(self, question, text):
        from . import claude_ask
        return claude_ask.finish_other_keys(question, text)

    def credential_present(self, config_dir: Path) -> bool | None:
        """Never reads the secret: `security find-generic-password` is called without
        -w, so it reports the item's presence and nothing else.

        None when the keychain cannot be seen from here: a sandboxed process gets
        "not found" for an item that exists, so its "absent" is not evidence.
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
        if r.returncode != 0 and os.environ.get("SANDBOX_RUNTIME"):
            return None
        return r.returncode == 0

    def login_argv(self) -> list[str]:
        return ["claude", "/login"]

    def logout_argv(self) -> list[str]:
        return ["claude", "auth", "logout"]

    def identity(self, env: dict) -> str | None:
        """Asked of `claude auth status`, which answers from its own records - ocp
        reads no credential and no .claude.json to learn it (AGENTS.md rule 1).
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
        # Never created, linked or copied: the credential file the installs that
        # do not use the Keychain keep (AGENTS.md rule 1), claude's own config,
        # and its machine state. `.claude.json` is read in exactly one place, by
        # the rule as amended (T745): `profile`, three fields, nothing else.
        return {".credentials.json", ".claude.json", "statsig", "backups"}

    def profile(self, config_dir: Path) -> dict:
        """The subscription's start and its rate-limit tiers, out of
        `.claude.json`'s `oauthAccount`, and nothing else: the file also holds
        the account's email and identifiers, which this function never returns,
        keeps or logs. Each value is checked for the shape it must have, so a
        field that held anything else is dropped rather than passed on. Read
        again only when the file changes (it can be large)."""
        path = config_dir / ".claude.json"
        try:
            st = path.stat()
        except OSError:
            return {}
        stamp = (st.st_mtime_ns, st.st_size)
        hit = _PROFILES.get(path)
        if hit and hit[0] == stamp:
            return dict(hit[1])
        out: dict = {}
        try:
            oa = json.loads(path.read_text()).get("oauthAccount")
            oa = oa if isinstance(oa, dict) else {}
            started = oa.get("subscriptionCreatedAt")
            out = {"subscription_started": _iso_epoch(started),
                   "tiers": [t for t in (oa.get("userRateLimitTier"), oa.get("organizationRateLimitTier"))
                             if isinstance(t, str) and _TIER.fullmatch(t)]}
        except (OSError, ValueError, AttributeError):
            out = {}
        _PROFILES[path] = (stamp, out)
        return dict(out)

    def memory_root(self, config_dir: Path) -> Path:
        return config_dir / "projects"

    def memory_project_dir(self, repo: str) -> str:
        """Claude Code's own path encoding: every non-alphanumeric character
        mapped to '-'."""
        return re.sub(r"[^A-Za-z0-9]", "-", repo)


# --------------------------------------------------------------- screen text
# The markers below read claude's own screens; the reader they feed on
# (Orca's terminal client, its Blocker/Dialog vocabulary, the composer-box and
# dialog-panel parsing every vendor's screen_blocker shares) stays generic in
# orca.py, reached here the same late way (design/04 §4.3: orca.py carries no
# vendor's own screen text).

# claude's folder-trust screen ("Quick safety check: Is this a project you
# created or one you trust?").
TRUST_PROMPT_MARKER = "Is this a project you created or one you trust"
# The line the prompt names its folder under. Wrapped at terminal width with
# no separator, so its continuation lines are joined bare, not with a space.
WORKSPACE_MARKER = "Accessing workspace:"
# Verified against a live prompt: one Down off the default cursor ("No,
# exit") lands on "Yes, I trust this folder"; a bare Enter then confirms it.
# A bare Enter with no Down confirms "No, exit" instead.
TRUST_PROMPT_DOWN_ARROW = "\x1b[B"


def _trust_screen(orca, tail: list[str]) -> str | None:
    """`Driver.trust_screen`'s contract: "" for a shown dialog whose folder a
    missing workspace line, a cut-off or a mid-redraw screen leaves unparsed."""
    text = "\n".join(str(x) for x in tail)
    if TRUST_PROMPT_MARKER not in text:
        return None
    lines = text.split("\n")
    try:
        start = next(i for i, ln in enumerate(lines) if WORKSPACE_MARKER in ln) + 1
    except StopIteration:
        return ""
    parts = []
    for ln in lines[start:]:
        stripped = ln.strip()
        if not stripped or TRUST_PROMPT_MARKER in stripped:
            break
        parts.append(stripped)
    return "".join(parts)


# Claude Code's backgrounded-session picker: the conversation moved to the
# background and the screen is a list of sessions. Its new-session box has the
# ordinary composer shape - rule/marker/rule - so the shape alone cannot tell it
# from the agent's own prompt, and text typed there goes to a new session, not
# the conversation. Its hint line does, where the conversation's own says
# "shift+tab to cycle". The picker's hint has two forms, found in a scratch
# tmux session on Claude Code 2.1.278 (purser's #1294, three runs, one
# session in the list): "enter to open" when another session is selected,
# "enter to return" when the current one is - which is what Left gives - so
# "enter to return" is not a marker. "space to reply" is in both, and a
# separate #1294 fleet read (31 live terminals, 2026-09-21) found it on 1 of
# them, with the banner. The one earlier case where it appeared with no
# banner (#1144, harbor) is not a hole in that: it is the picker with its
# banner scrolled out of the window, and the miss fails closed to UNREADABLE
# there, never to a false BACKGROUNDED (see _hint_below_box: the position
# guard is the same code path for both markers).
#
# "enter to open" alone is not enough: it is ordinary English, and any agent that
# reports on this feature (this docstring, a review of it) writes it in its
# own transcript, which sits in the same rows the naive search covered - #1252,
# measured on all 8 live agents where the window reached 1-2 rows of the
# agent's own output. So the hint is required at or below the composer's own
# box, never above it in the transcript.
#
# That alone still under-determines it (auditor's #1285 B1): a screen where
# the box's own hint happens to say a picker phrase for some other reason
# would pass a position check alone, and reading nothing there as "in the
# way" is exactly the bug 7f01f52 exists to fix. So the picker's own banner -
# "...conversation moved to the background...", specific text neither an
# ordinary composer's hint nor a session's own summary line would produce -
# is required above the box too. A session list's own shape (a bullet, a
# gap, a summary column) was tried first and dropped: auditor measured three
# real-picker variants - a short title with no summary gap, a bullet outside
# the two guessed at, a cursor glyph ahead of the selected row - that the
# column-alignment guess missed, while the banner is the one thing all of
# them, and both fixtures here, say word for word.
_BACKGROUNDED_HINTS = ("enter to open", "space to reply")
_BACKGROUND_BANNER = "moved to the background"


def _has_picker_hint(row: str) -> bool:
    return any(h in row for h in _BACKGROUNDED_HINTS)


def _picker_box(orca, tail: list) -> tuple[int, int] | None:
    """(banner_top, hint) when the composer's own hint carries a picker
    phrase ("enter to open" or "space to reply") and the picker's own banner
    is above it - the picker, unambiguously.
    None when neither is there - an ordinary composer, whose own hint never
    says this and whose transcript quoting the words is above the box, not
    below it (auditor's #1285 S2/S3).

    A third case - the hint is there but the banner is not - is not this
    function's to decide: it is a screen shaped like the picker that this
    code does not otherwise recognise, and `screen_blocker` fails closed on
    it rather than reading it as nothing in the way (#1285 B1).
    """
    box = orca._composer_box(tail)
    if box is None:
        return None
    top, bottom = box
    hint = next((i for i in range(bottom, len(tail))
                 if _has_picker_hint(str(tail[i]))), None)
    if hint is None:
        return None
    if not any(_BACKGROUND_BANNER in str(x) for x in tail[:top]):
        return None
    return top, hint


def _hint_below_box(orca, tail: list) -> bool:
    """Whether the composer's own hint says a picker phrase ("enter to
    open" or "space to reply"), whether or not its banner confirms it. Used
    only to tell an unconfirmed match from a clean composer: the hint alone
    is the one thing an ordinary conversation's composer never says
    (auditor's #1285 B1)."""
    box = orca._composer_box(tail)
    if box is None:
        return False
    _top, bottom = box
    return any(_has_picker_hint(str(tail[i])) for i in range(bottom, len(tail)))


def _describe_backgrounded(orca, tail: list, top: int) -> str:
    """The picker's banner and session list above its own box, quoted - the
    same hostile-text care as describe_modal, since the agent paints this
    screen too. Never the box or the hint themselves, which say nothing
    about the conversation. Called only once `_picker_box` has confirmed the
    banner is there - not a guarantee against every false match (auditor's
    #1285 N4: an agent's own transcript can quote this exact fixture, banner
    and all), but that case clears itself on the next read, unlike a false
    BACKGROUNDED that never scrolls."""
    lines = [orca.textsafe.oneline(str(x).strip()) for x in tail[:top]]
    shown = [ln for ln in lines if ln and not orca._COMPOSER_RULE.match(ln) and ln != "❯"]
    return " / ".join(shown[:3])[:orca.DETAIL_CHARS]


# A turn in progress draws a spinner row just above the composer ("✻ Honking… (2m 0s · ↓ 6.2k
# tokens)") and "esc to interrupt" among the hints under it; a finished turn's line reads
# "✻ Sautéed for 1m 26s", with no ellipsis (six samples, T592). Rows checked above the composer.
_SPINNER_ROW = re.compile(r"^\S\s+\S+…(\s|$)")
_SPINNER_ROWS = 6


def _idle_with_background(orca, tail: list) -> bool:
    """`Driver.idle_with_background` on Claude Code's layout: the hint row counts background
    work, and neither the spinner nor "esc to interrupt" is drawn (T458)."""
    box = orca._composer_box(tail)
    if box is None or not orca.footer_background(tail):
        return False
    if any("esc to interrupt" in str(r) for r in tail[box[1] + 1:]):
        return False
    return not any(_SPINNER_ROW.match(str(r).strip()) for r in tail[max(0, box[0] - _SPINNER_ROWS):box[0]])


def _screen_blocker(orca, tail: list) -> "orca.Blocker | None":
    """`Driver.screen_blocker` on Claude Code's layout, from rows already read."""
    footer = orca._footer_row(tail)
    if footer is not None:
        # Deliberately not widened: a long command's title stays "", so autoaccept never
        # matches a standing rule against a dialog it could not read whole.
        dialog = orca.parse_dialog(tail, footer)
        return orca.Blocker(orca.MODAL, orca.describe_modal(tail, footer),
                            title=dialog.title if dialog else "")
    picker = _picker_box(orca, tail)
    if picker is not None:
        top, _hint = picker
        return orca.Blocker(orca.BACKGROUNDED, _describe_backgrounded(orca, tail, top))
    if _hint_below_box(orca, tail):
        # The composer's own hint says the picker's words but its banner is
        # not above the box: a picker shaped differently than the two we
        # have measured, not a clean composer. Fail closed rather than read
        # it as nothing in the way and type into what might be its
        # new-session box (auditor's #1285 B1) - the same failure 7f01f52
        # exists to fix, reopened by trusting a guess about session-row shape.
        return orca.Blocker(orca.UNREADABLE)
    if orca._composer_shape(tail):
        return None
    # Not the composer's shape either - a screen mid-scroll, a different
    # layout, a read that landed somewhere else. UNREADABLE goes through the
    # same decision as any other block and clears the moment a read
    # recognises the shape again.
    return orca.Blocker(orca.UNREADABLE)
