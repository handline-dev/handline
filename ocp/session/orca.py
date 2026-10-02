"""Talking to the Orca terminal an agent is running in.

This is what makes "hand off, park, resume" actually reclaim context (team
model section 2, axis 3: the cost of an async handoff is that the requester
returns its context). Recording the handoff is only half of it - without
clearing the session the context never drops, so a context trigger fires again
on the very next tick and the agent hands off forever.

`/clear` starts a fresh session in the same process: measured on a live agent,
total_input_tokens went 97801 -> 0 and the session id changed, while the
terminal, worktree and account stayed put. That is exactly the "keep the house,
empty the memory" shape we want, and it is why an agent does not need to be
destroyed and recreated to be cheap.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

from ..infra import textsafe
from . import backends


# Sending /clear mid-turn would discard work in flight, so we never force it.
# The daemon asks with a short timeout because this call blocks its whole tick:
# waiting two minutes on one busy agent stops supervision of every other agent.
# A busy agent is not a failure, it is just busy, so we come back next tick.
#
# Measured 2026-09-17 against the shells left behind by three closed sessions,
# because three reviews in a row declined to trust this probe against a terminal
# with no TUI in it. Do not trust it there: it is not consistent. The short
# probe said not-satisfied every time, at the full 3.1s, and so did two of the
# three long waits - but the third returned satisfied after 63.7s on a terminal
# whose session had ended half an hour earlier and did not start again for
# another ten minutes. So "no TUI" does not reliably read as "not idle", and
# nothing may be sent to a terminal on the strength of this probe alone when we
# have any other way to know whether a session is there. The process table is
# that other way, and it is in observe.py.
#
# The cost is consistent even when the answer is not: a "no" always takes the
# whole timeout, so probing a session that has already exited pays two minutes
# for an answer that was free elsewhere.
IDLE_TIMEOUT_MS = 120_000
IDLE_PROBE_MS = 3_000


def available() -> bool:
    return backends.current().available()


def ensure_repo(path: str) -> tuple[str, str]:
    """Make sure the terminal host knows `path` as a repo: (state, what to tell the founder).
    The states are the backend's: 'known', 'added', 'not_git', 'no_orca' and 'failed'."""
    return backends.current().ensure_repo(path, available())


def terminal_handle() -> str | None:
    """The terminal we are running inside, if the host launched us."""
    return backends.current().own_terminal()[0]


def keeps_labels() -> bool:
    """Whether the configured host keeps worktree labels at all, there or not."""
    return backends.current().supports_labels


def has_labels() -> bool:
    """Whether the host is there and keeps worktree labels and parents (Orca does, tmux does not)."""
    return backends.current().supports_labels and available()


def host_cli(args: list[str], timeout: int = 180) -> tuple[bool, str]:
    """One command to the host's own CLI; only for what `has_labels` gates."""
    return backends.current().host_cli(args, timeout)


def knows_handle(handle: str) -> bool:
    """Whether `handle` looks like one the configured host made."""
    return backends.current().knows_handle(handle)


def has_runs() -> bool:
    """Whether the host is there and draws a team (runs and tasks with workers under them)."""
    return backends.current().supports_runs and available()


def host_name() -> str:
    return backends.current().name


def missing() -> str:
    """What to say when `available()` is False."""
    return f"{backends.current().name} is not on PATH"


def worktree_id() -> str | None:
    """The host's id for the worktree of the terminal we are running inside."""
    return backends.current().own_terminal()[1]


def wait_idle(handle: str, timeout_ms: int = IDLE_TIMEOUT_MS) -> bool:
    """Whether the agent is done working.

    `ok` only says the wait command ran; `result.wait.satisfied` is the answer.
    Reading `ok` alone treats a timeout as "idle" and clears mid-turn.

    For a vendor whose working state Orca does not read (`idle_from_screen`),
    Orca's yes is only a precondition, and the screen is read until it agrees
    or the time is up.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    if not _orca_idle(handle, timeout_ms):
        return False
    drv = _driver_for(handle)
    if not drv.idle_from_screen:
        return True
    while True:
        term = _screen(handle)
        tail = term.get("tail") if term else None
        if isinstance(tail, list) and term.get("source") == "screen" \
                and drv.idle_on_screen(tail):
            return True
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(SCREEN_POLL_S, left))


# How often `wait_idle` rereads a screen that does not yet show idle.
SCREEN_POLL_S = 1.0


def _orca_idle(handle: str, timeout_ms: int) -> bool:
    return backends.current().wait_idle(handle, timeout_ms)


# Drivers already looked up inside the running `driver_scope`, by handle. Each
# lookup opens a database connection, and one clear or close asks several times.
_scope = threading.local()


@contextlib.contextmanager
def driver_scope():
    """Within it, `_driver_for` answers each handle from the database once.
    Nested scopes share the outer one; nothing outlives the outermost."""
    outer = getattr(_scope, "memo", None) is not None
    if not outer:
        _scope.memo = {}
    try:
        yield
    finally:
        if not outer:
            _scope.memo = None


def _driver_for(handle: str):
    """The driver of the agent ocp recorded in this terminal, or the default
    vendor's for one it did not launch (a probe, a human's own)."""
    memo = getattr(_scope, "memo", None)
    if memo is not None and handle in memo:
        return memo[handle]
    drv = _lookup_driver(handle)
    if memo is not None:
        memo[handle] = drv
    return drv


def _lookup_driver(handle: str):
    # Late: vendors and db sit above this module.
    import sqlite3
    from ..team import agents
    from .. import db, vendors
    try:
        con = db.connect()
        try:
            agent = db.agent_of_terminal(con, handle)
        finally:
            con.close()
        if agent:
            return vendors.for_agent(agent)
    except (OSError, sqlite3.Error, agents.ConfigError, vendors.UnknownVendor):
        pass
    return vendors.get(agents.DEFAULT_VENDOR)


def clear_command(handle: str) -> str:
    """What is typed at this terminal's agent to clear its context - the
    vendor's own, for a message that names it."""
    return _driver_for(handle).clear_command()


def send(handle: str, text: str, enter: bool = True) -> tuple[bool, str]:
    return backends.current().send(handle, text, enter)


def screen_text(handle: str) -> str | None:
    """What this terminal is rendering right now, or None if it could not be
    read - never mistaken for "nothing is showing"."""
    term = _screen(handle)
    tail = term.get("tail") if term else None
    if not isinstance(tail, list) or term.get("source") != "screen":
        return None
    return "\n".join(tail)


def _trust_screen_showing(handle: str) -> bool | None:
    """Whether this terminal shows its vendor's folder-trust screen, None when
    the screen could not be read - never mistaken for "not showing"."""
    text = screen_text(handle)
    return None if text is None else _driver_for(handle).trust_screen(text.split("\n")) is not None


def trust_prompt_showing(handle: str) -> bool:
    """Is this session sitting at its vendor's folder-trust screen?

    Read before typing anything: a handoff followed by Enter, the daemon's
    ordinary wake, answers the default option on this screen every time -
    "No, exit" on claude's. An unreadable screen answers False, fine for a gate
    that then types nothing.
    """
    return _trust_screen_showing(handle) is True


def trust_prompt_gone(handle: str) -> bool:
    """True only when the screen was read and it is not a folder-trust screen:
    the answer for something that lets go of a stall, which an unreadable
    screen must not."""
    return _trust_screen_showing(handle) is False


def wait_for_trust_prompt(handle: str, showing: bool, timeout_s: float = 5.0,
                          poll_s: float = 0.5) -> bool:
    """Poll until `trust_prompt_showing(handle)` is `showing`, or give up.

    The daemon uses it after accepting the dialog, to wait for the screen to
    clear: the vendor's redraw is not instant, and checking on the very next
    line risks reading the screen it was still drawing.
    """
    deadline = time.time() + timeout_s
    while trust_prompt_showing(handle) != showing:
        if time.time() >= deadline:
            return False
        time.sleep(poll_s)
    return True


def trust_prompt_folder(handle: str) -> str | None:
    """The folder the trust dialog on this terminal's screen names, or None
    when it is not showing one, or is but the folder could not be read -
    `trust_screen`'s "" collapses into None here, since a caller of this one
    has nothing to do with "showing but unreadable" except what it already
    does with "not showing": stop rather than press ahead.

    Never the path a caller has on record for the session: the two can
    differ (a stale record, a reused terminal, a human's own `cd`), and
    accepting it accepts whatever is actually on screen.
    """
    text = screen_text(handle)
    if text is None:
        return None
    return _driver_for(handle).trust_screen(text.split("\n")) or None


def confirm_trust(handle: str) -> tuple[bool, str]:
    """Press this vendor's trust answer, blind to which folder is on screen -
    callers decide *whether* to press it. Writes nothing to any vendor's own
    config file.
    """
    ok, out = send(handle, _driver_for(handle).trust_answer(), enter=False)
    if not ok:
        return False, f"could not accept the trust dialog: {out[:200]}"
    ok, out = send(handle, "")
    return ok, "trusted" if ok else f"could not confirm the trust prompt: {out[:200]}"


def create_terminal(command: str, cwd: str | None = None,
                    title: str | None = None) -> str | None:
    """Start a fresh terminal running `command`, or None if the host could not.

    `cwd` None runs inside whatever directory this process is already in, which
    is what a throwaway probe wants (a directory `command` itself `cd`s into,
    not one the host has to know about).
    """
    return backends.current().create_terminal(command, cwd, title)[0]


def open_terminal(command: str, cwd: str | None = None,
                  title: str | None = None) -> tuple[str | None, str]:
    """`create_terminal` with the reason it could not: (handle, "") or (None, why). A terminal that
    started but whose handle could not be read is (None, "")."""
    return backends.current().create_terminal(command, cwd, title)


def close_terminal(handle: str, tab: bool = True) -> tuple[bool, str]:
    """Close one terminal, and its tab unless told to leave the tab standing."""
    return backends.current().close_terminal(handle, tab)


def run_create(objective: str) -> str | None:
    """Start an orchestration Run - the card a team's work hangs under, on a host that draws one.

    It also *binds* the calling terminal to that Run, which is why this is
    called once and remembered: calling it again makes a second card with the
    same name and moves the binding to it, and a team split across two cards
    is a tree that draws half of itself.
    """
    return backends.current().run_create(objective)


def run_current() -> str | None:
    """The Run this terminal is already bound to, if any."""
    return backends.current().run_current()


def run_exists(run: str) -> bool:
    return backends.current().run_exists(run)


def run_use(run: str) -> bool:
    """Bind this terminal to an existing Run, so tasks land in the team's card."""
    return backends.current().run_use(run)


def task_create(spec: str, title: str, display_name: str, run: str,
                parent: str | None = None) -> str | None:
    """A task in the Run, optionally under another - this is the tree the host draws."""
    return backends.current().task_create(spec, title, display_name, run, parent)


def dispatch(task: str, handle: str, run: str) -> str | None:
    """Bind a task to a terminal, which is what makes it a worker in the card."""
    return backends.current().dispatch(task, handle, run)


def submit(handle: str, text: str, wait_submit_s: int) -> tuple[bool, str]:
    """Type `text` and Enter, asking a host that can to watch the prompt land (it never resends)."""
    return backends.current().send(handle, text, True, wait_submit_s)


def create_worktree(repo: str, name: str, base_ref: str) -> tuple[str | None, str]:
    """A new worktree named `name` branched from `base_ref`: (its path, "") or (None, why not)."""
    return backends.current().create_worktree(repo, name, base_ref)


def focus(handle: str) -> tuple[bool, str]:
    """Bring this terminal to the front in the host's window.

    The one thing the screen does that is not about state: the reader stops
    deciding and starts working, in the agent's own session, with everything it
    can see. Nothing is typed and nothing is read - which is why it is safe on
    an agent that is mid-turn, where every other key here is not.
    """
    return backends.current().focus(handle)


def live_terminals() -> set[str] | None:
    """Every handle the host currently has, or None when it could not say.

    None is not an empty set. Reading "I could not ask" as "there are none"
    would tear down a healthy team the first time the host was slow to answer.
    """
    return backends.current().list_terminals()


def alive(handle: str) -> bool:
    handles = live_terminals()
    return handles is not None and handle in handles


def idle_now(handle: str) -> bool:
    """Is the agent idle right now? Does not block the caller waiting."""
    return wait_idle(handle, IDLE_PROBE_MS)


# What Claude Code draws under a modal it opens itself ("Teach auto mode about
# your environment?", trust prompts). Anything typed plus Enter answers it.
MODAL_MARKERS = ("Enter to confirm", "Esc to cancel")
MODAL_ROWS = 3


def _screen(handle: str, limit: int = 40) -> dict | None:
    """The rendered screen, or None when it cannot be read. `limit` is how
    many of the bottom rows come back - `dialog_on_screen` widens it past the
    default when a dialog's own top border has scrolled out of a narrower read."""
    return backends.current().read_screen(handle, limit)


# Why a line typed now would not land, named.
MODAL, BACKGROUNDED, UNREADABLE = "modal", "backgrounded", "unreadable"


@dataclass(frozen=True)
class Blocker:
    kind: str
    # What the screen itself says: a dialog's title and options, or the
    # background picker's banner and sessions. Quoted from the screen, never
    # classified. Empty for UNREADABLE.
    detail: str = ""
    # The dialog's own title, raw from parse_dialog - not reconstructed from
    # `detail`, which is a rendering (quoted, joined, truncated) that a title
    # with a quote in it or over DETAIL_CHARS long does not round-trip through.
    # Empty for anything but MODAL, or a MODAL parse_dialog itself refuses.
    title: str = ""

    def reason(self) -> str:
        if self.kind == MODAL:
            base = "a dialog is open on its screen"
        elif self.kind == BACKGROUNDED:
            base = "its conversation moved to the background"
        else:
            return "its screen could not be read"
        return f"{base}: {self.detail}" if self.detail else base


# A dialog's panel opens with a rule; the title is the first line under it. The
# exit confirmation draws it with U+2594 (measured), the tool permission prompt
# with U+2500 (a founder's paste of a live one, T277).
_RULE = re.compile(r"^[\u2594\u2500]{8,}$")
_OPTION = re.compile(r"^(?:\u276f\s*)?\d+\.\s+\S")
DETAIL_CHARS = 200

# An agent can paint its own screen, so a dialog's title and options are
# exactly the hostile-text case textsafe.oneline() exists for - applied at the
# source, the earliest point this text leaves a screen, before it reaches a
# log line or a request body neither of us sees the sink of.
def describe_modal(tail: list[str], footer: int) -> str:
    """The dialog above row `footer` in its own words: its title, then its
    numbered options. Where no rule marks where the dialog begins, the last
    three lines above the footer, verbatim - a quotation, so a layout this has
    not seen still says something true."""
    lines = [textsafe.oneline(str(x).strip()) for x in tail[:footer]]
    top = next((i for i in range(len(lines) - 1, -1, -1) if _RULE.match(lines[i])), None)
    if top is None:
        return " / ".join([ln for ln in lines if ln][-3:])[:DETAIL_CHARS]
    body = [ln for ln in lines[top + 1:] if ln]
    options = [re.sub(r"^\u276f\s*", "", ln) for ln in body if _OPTION.match(ln)]
    text = f'"{body[0]}"' if body else ""
    if options:
        text += " - " + " / ".join(options)
    return text[:DETAIL_CHARS]


def _footer_row(tail: list) -> int | None:
    """The index of the dialog's footer among the bottom rows, or None."""
    for i in range(max(0, len(tail) - MODAL_ROWS), len(tail)):
        if any(m in str(tail[i]) for m in MODAL_MARKERS):
            return i
    return None


@dataclass(frozen=True)
class Dialog:
    """A confirmation dialog read whole: its title, its numbered options, its
    footer - and `body`, every non-empty line of the panel verbatim (the
    title, the raw numbered-option lines, and anything else - free text, a
    sub-detail under an option), so a caller deciding what Esc does can look
    at the whole panel, not only the part this parse already turned into data."""
    title: str
    options: tuple[tuple[int, str], ...]
    footer: str
    body: tuple[str, ...] = ()


_OPTION_LABEL = re.compile(r"^(?:\u276f\s*)?(\d+)\.\s+(\S.*)$")

# What `parse_dialog` shows as the title when a dialog is real but its own
# top rule and title are still off-screen at `_DIALOG_READ_MAX` - a stand-in
# that says so, never a guess dressed up as the real one.
TRUNCATED_TITLE = "(command not fully shown - its start is off-screen)"


def parse_dialog(tail: list[str], footer: int, allow_truncated: bool = False) -> Dialog | None:
    """The dialog above row `footer` as data, or None when any part of it is
    not there to read: no rule opening the panel, no title, or fewer than two
    options numbered 1, 2, ... in order. What cannot be parsed is not guessed.

    `allow_truncated` is `dialog_on_screen`'s last resort, once widening the
    read up to `_DIALOG_READ_MAX` still finds no rule: every visible line
    becomes the body (there is no title line left to skip - the true one is
    further back than even that read reached), and `TRUNCATED_TITLE` stands
    in for it rather than the founder being shown no dialog at all for a
    command whose preview alone outgrew the widest read this will make."""
    lines = [textsafe.oneline(str(x).strip()) for x in tail[:footer]]
    top = next((i for i in range(len(lines) - 1, -1, -1) if _RULE.match(lines[i])), None)
    if top is None:
        if not allow_truncated:
            return None
        body = [ln for ln in lines if ln]
        options = [(int(m[1]), m[2].strip()) for m in map(_OPTION_LABEL.match, body) if m]
        if len(options) < 2 or [n for n, _ in options] != list(range(1, len(options) + 1)):
            return None
        return Dialog(TRUNCATED_TITLE, tuple(options),
                      textsafe.oneline(str(tail[footer]).strip()), tuple(body))
    body = [ln for ln in lines[top + 1:] if ln]
    if not body or _OPTION_LABEL.match(body[0]):
        return None
    options = [(int(m[1]), m[2].strip()) for m in map(_OPTION_LABEL.match, body[1:]) if m]
    if len(options) < 2 or [n for n, _ in options] != list(range(1, len(options) + 1)):
        return None
    return Dialog(body[0], tuple(options), textsafe.oneline(str(tail[footer]).strip()), tuple(body))


# The whole label of an option that leaves the world as it is, and nothing wider:
# 'No, exit' and 'No, and tell Claude what to do differently' are choices, and are
# not in it. A label not here is not pressed for, however harmless it looks.
INERT_LABELS = frozenset({"stay", "cancel", "no", "back", "keep", "do nothing",
                          "not now", "never mind"})
# Checked against a live terminal, not only against fixtures: orcapipe (the
# founder) sent this exact byte via `orca terminal send --text $'\033'` to two
# real, stuck Claude Code dialogs on 2026-09-21 - orcapipe.auditor and
# harbor.marina, both showing "Background work is running" - and both cleared
# on the next screen read. `accepted:true` and the dialog actually closing are
# different claims; both were seen to hold.
ESCAPE = "\x1b"


_ESC_MARK = re.compile(r"\(esc\)\s*$", re.IGNORECASE)


def _norm_label(label: str) -> str:
    return re.sub(r"\s*\(esc\)$", "", label.strip().lower()).rstrip(".!")


def _inert(label: str) -> bool:
    return _norm_label(label) in INERT_LABELS


# _ESC_MARK (case-insensitive, a trailing space allowed) and the strip inside
# _norm_label (case-sensitive after its own .lower(), no trailing space allowed)
# are deliberately different patterns, not a drifted duplicate: _ESC_MARK only
# ever has to notice the mark is there at all, on raw screen text; _norm_label
# runs on text it has already lowercased and stripped itself. _inert reaches it
# only after _ESC_MARK has looked. They agree because each does the normalising its own job
# needs, not because they are meant to be the same regex.


# What Claude Code asks when a turn ends on the session limit: none of its
# options only declines ('Stop and wait' is a choice that stops the session), so
# the inert-option rule cannot admit it. Esc was measured on a live one on
# 2026-09-21 (orcapipe.chat, 'resets 3:30pm'): the dialog closed, the session
# stayed at its prompt, nothing was chosen. Named by the two options that make
# it this dialog rather than by its title, which is generic, and by their start
# only, since the second carries the reset time.
#
# The claim has an expiry: it is one observation of what Esc does. If Claude
# Code ever lets Esc pick the highlighted option, decline_dialog's re-read would
# report CLEARED just the same while ocp had chosen 'Stop and wait' - so when
# its behaviour changes, re-measure on a live one that Esc leaves the session at
# its prompt with the limit still standing and that nothing else was chosen.
def _usage_limit_dialog(dialog: Dialog) -> bool:
    labels = [label for _, label in dialog.options]
    return (any(lb.startswith("Stop and wait for limit to reset") for lb in labels)
            and any(lb.startswith("Wait here, then continue automatically") for lb in labels))


def declining_key(dialog: Dialog) -> tuple[str | None, str]:
    """(key, why not) - Escape, when the dialog itself says Esc cancels it and
    offers an option that only declines, or is the usage-limit dialog whose Esc
    was measured; otherwise no key and the reason.

    Never a key that chooses: Enter, a digit, an arrow. The one key is Esc, and
    it is pressed only where the footer says it cancels AND the options include
    one that is only 'stay' or 'no' - the evidence that cancelling leaves things
    as they are. A dialog whose declining option is 'No, exit' has none: there
    Esc leaves the session, which is a choice.

    When an option itself is marked '(esc)' - Claude Code does this on some
    dialogs (the permission prompt's own fixture below) - that IS what Esc
    does, read off the screen rather than inferred from a different line: the
    trust dialog's 'No, exit (esc)' is not inert, and no other option's
    wording, however harmless, excuses pressing over what the screen already
    said. The mark is looked for across the WHOLE panel, not only the option
    labels this parse turned into data: a title reading 'Exiting will stop
    tasks (esc)', or a free-text line like 'Press Esc to exit (esc)' between
    the options, is not an option this can evaluate, so a mark found there
    refuses too, the same as one on an option that turns out not to be inert.
    Only a dialog with no mark anywhere falls back to "some option is only a
    decline", which is the evidence available when nothing names Esc directly -
    or is the one dialog whose Esc was measured rather than inferred.
    """
    if "Enter to confirm" not in dialog.footer or "Esc to cancel" not in dialog.footer:
        return None, "its footer does not say Enter confirms and Esc cancels"
    stray = [ln for ln in dialog.body if _ESC_MARK.search(ln) and not _OPTION_LABEL.match(ln)]
    if stray:
        return None, f"the (esc) mark appears where it cannot be evaluated: {stray[0]!r}"
    marked = [label for _, label in dialog.options if _ESC_MARK.search(label)]
    if marked:
        if len(marked) > 1:
            return None, "more than one option is marked (esc)"
        if not _inert(marked[0]):
            return None, f"the option marked (esc) is not one that only declines: {marked[0]!r}"
        return ESCAPE, ""
    if _usage_limit_dialog(dialog):
        return ESCAPE, ""
    if not any(_inert(label) for _, label in dialog.options):
        return None, "none of its options only declines"
    return ESCAPE, ""



# The composer's own box: two full-width '─' rules around one content row.
# How many rows sit below it varies by agent (auditor's #1144 fleet read), so
# the triple is searched for rather than assumed to sit a fixed distance from
# the end.
_COMPOSER_RULE = re.compile(r"^[─]{8,}$")


def _composer_box(tail: list) -> tuple[int, int] | None:
    """(top, bottom) row indices of the composer's rule/content/rule triple,
    or None. The triple is searched for, not assumed a fixed distance from
    the end (auditor's #1144 fleet read)."""
    for k in range(3, 9):
        if len(tail) < k:
            break
        top, bottom = len(tail) - k, len(tail) - k + 2
        if (_COMPOSER_RULE.match(str(tail[top]).strip())
                and _COMPOSER_RULE.match(str(tail[bottom]).strip())):
            return top, bottom
    return None


def _composer_shape(tail: list) -> bool:
    """Whether the tail holds a recognized composer box - never what is in
    it; see screen_blocker for why."""
    return _composer_box(tail) is not None


def session_on_screen(handle: str) -> bool | None:
    """Whether this terminal's screen shows an agent's session: the vendor's composer box, a dialog's
    footer, or a turn the vendor's screen says is running. A shell prompt is none of those, so this is
    the second witness to the process table: it is what stops a launch line typed at a live session
    when the table failed to list it. None when the screen cannot be read - not "no session"."""
    term = _screen(handle)
    tail = term.get("tail") if term else None
    if not isinstance(tail, list) or term.get("source") != "screen":
        return None
    if _composer_shape(tail):
        return True
    rows = [str(r) for r in tail[-MODAL_ROWS - 3:]]
    if any(m in r for r in rows for m in MODAL_MARKERS):
        return True
    return _driver_for(handle).working_on_screen(tail) is not None


def screen_blocker(handle: str) -> Blocker | None:
    """What on the screen a typed line would answer or merge into, from one read.

    A dialog draws its footer on the bottom rows; above them is the transcript,
    which may quote those words without anything being asked. Fail-closed: a
    screen that cannot be read blocks, because the cost of a wrong no is an
    answer given to a question nobody asked.

    The composer's own content is never read: `draft` is Orca's UI composer, and
    a person's typing and ocp's own echoed sends land there identically, with
    the text on no screen row. Text a person types at an agent's terminal can
    therefore land inside ocp's next instruction as one submitted prompt.
    """
    term = _screen(handle)
    tail = term.get("tail") if term else None
    # Any source but the screen is the accumulated stream, whose history is the
    # conversation, not what is drawn now.
    if not isinstance(tail, list) or term.get("source") != "screen":
        return Blocker(UNREADABLE)
    return _driver_for(handle).screen_blocker(tail)


# How often a redraw or a recheck may reuse the same screen read, so a caller
# on a short cycle - the dashboard's own redraw, a block's RECHECK_S - does
# not reissue the read every time it asks. A `security` subprocess on that
# same cycle once froze the dashboard for its whole TTL (tui.LOGIN_TTL_S);
# this is that fix's shape, for this call. Callers that act on the read right
# now - a key press, a one-off report - use screen_blocker itself instead,
# which this wraps but never replaces.
SCREEN_CACHE_TTL_S = 5
_screen_cache: dict[str, tuple[float, Blocker | None]] = {}


def screen_blocker_cached(handle: str, not_before: float = 0.0) -> Blocker | None:
    """A cached read of `handle`'s screen, at most SCREEN_CACHE_TTL_S old.

    `not_before` refuses a cached read older than that instant: a read taken
    before a fact existed cannot answer a question about it - auditor's
    #1721 BLOCKING, a read from before a second permission prompt was raised
    read as "no dialog" and answered the new prompt with the old one's
    absence, deleting a wait that had not even been shown yet. A caller with
    no such fact in play passes nothing, and any cached read within the TTL
    still answers.
    """
    now = time.time()
    seen = _screen_cache.get(handle)
    if seen and seen[0] > not_before and now - seen[0] < SCREEN_CACHE_TTL_S:
        return seen[1]
    blocker = screen_blocker(handle)
    _screen_cache[handle] = (now, blocker)
    return blocker


CLEARED, STILL_OPEN, NOT_PRESSED = "cleared", "still-open", "not-pressed"
NO_DIALOG = "no dialog is open"
# How long a dialog is given to go after Esc, read again each time.
PRESS_SETTLE_S, PRESS_SETTLE_READS = 0.5, 4

# A dialog's own read starts at the ordinary width and, once a footer is
# seen at all, doubles up to this before falling back to TRUNCATED_TITLE - a
# long enough command (a heredoc script, say) pushes the panel's top rule
# and title past a 40-line read, and a real, answerable dialog would
# otherwise look like none is there.
_DIALOG_READ_START = 40
_DIALOG_READ_MAX = 400


def _read_dialog(handle: str, allow_truncated: bool = True,
                 term: dict | None = None) -> tuple[list[str] | None, int | None, Dialog | None]:
    """(tail, footer, dialog) for `handle`'s screen now - the one read every
    caller that looks at or presses a dialog goes through, so a long
    command's dialog (found only by widening) is exactly as pressable as it
    is visible; `dialog_on_screen` and `_press_dialog` used to each read the
    screen their own way, and only one of the two widened.

    `tail` is None when the screen could not be read at all; `footer` stays
    None when what was read has no dialog on it. `dialog` can still be None
    with both set - a footer with no dialog parse-able above it, at any
    width up to the cap, or (`allow_truncated=False`) one whose rule never
    turned up at all: ocp's own autonomous presses (`decline_dialog`,
    `stop_work_and_exit`) pass this, since pressing Esc on a dialog it could
    not read the whole of is a choice made on too little to make it safely -
    unlike a numbered press, which is a choice someone else already made
    and is confirmed here, not decided.

    `term` is a first read the caller already made at the default width, so a
    caller that also looks at the raw screen costs one read, not two."""
    if term is None:
        term = _screen(handle)
    limit = _DIALOG_READ_START
    while True:
        tail = term.get("tail") if term else None
        if not isinstance(tail, list) or term.get("source") != "screen":
            return None, None, None
        footer = _footer_row(tail)
        if footer is None:
            return tail, None, None
        maxed = limit >= _DIALOG_READ_MAX
        if maxed or any(_RULE.match(str(x).strip()) for x in tail[:footer]):
            return tail, footer, parse_dialog(tail, footer, allow_truncated=maxed and allow_truncated)
        limit = min(limit * 2, _DIALOG_READ_MAX)
        term = _screen(handle, limit)


def dialog_on_screen(handle: str) -> Dialog | None:
    """The confirmation dialog this terminal shows now, parsed, or None when there is none
    or it cannot be read - the bridge offers its options to the founder."""
    return _read_dialog(handle)[2]


def _press_dialog(handle: str, pick, key_name: str, allow_truncated: bool = True) -> tuple[str, str, str]:
    """(outcome, dialog title, why): press the key `pick(dialog)` names on the
    dialog on this terminal's screen, if - and only if - the screen just read
    says it may be pressed.

    The screen is read here, immediately before the key, and read again after
    it: what is pressed is what is on screen now, never what an earlier read
    said. Nothing is pressed on a screen that cannot be read, is not a
    dialog, or has a title or options that do not parse; a key that is sent
    and does not close the dialog is reported as still open, not repeated.
    """
    tail, footer, dialog = _read_dialog(handle, allow_truncated)
    if tail is None:
        return NOT_PRESSED, "", "its screen could not be read"
    if footer is None:
        return NOT_PRESSED, "", NO_DIALOG
    if dialog is None:
        return NOT_PRESSED, "", "its title or options could not be parsed"
    key, why = pick(dialog)
    if key is None:
        return NOT_PRESSED, dialog.title, why
    ok, out = send(handle, "") if key == ENTER else send(handle, key, enter=False)
    if not ok:
        return NOT_PRESSED, dialog.title, f"the key could not be sent: {out[:120]}"
    for _ in range(PRESS_SETTLE_READS):
        time.sleep(PRESS_SETTLE_S)
        after_tail, after_footer, after_dialog = _read_dialog(handle, allow_truncated)
        if after_tail is None:
            continue  # not evidence either way - the same screen fails closed everywhere else
        if after_footer is None:
            return CLEARED, dialog.title, ""
        if after_dialog is not None and after_dialog.title != dialog.title:
            # Not the dialog the key was pressed on - that one is gone, whatever
            # this new one is; still-open must never be counted against a
            # title that is not what is actually still on screen, since that
            # is what a loop is measured by.
            return CLEARED, dialog.title, ""
    return STILL_OPEN, dialog.title, f"the dialog is still on screen after {key_name}"


def _digit_key(dialog: Dialog, number: int) -> tuple[str | None, str]:
    """(key, why not) - the one key that presses option `number` of `dialog`: a digit, and only for
    an option the dialog shows. The one digit-press path (`press_option`, `answer_dialog`)."""
    if not 1 <= number <= 9:      # `10` would be sent as the keys "1", "0": the dialog takes the 1
        return None, f"option {number} cannot be pressed by one key"
    if any(n == number for n, _ in dialog.options):
        return str(number), ""
    return None, f"the dialog has no option {number}"


def press_option(handle: str, number: int) -> tuple[str, str, str]:
    """Press option `number` of the dialog on screen now, if it has one, and check that the
    dialog went (`_press_dialog`)."""
    return _press_dialog(handle, lambda dialog: _digit_key(dialog, number), f"option {number}")


def decline_dialog(handle: str) -> tuple[str, str, str]:
    """Press Esc on the dialog on this terminal's screen, if - and only if -
    the screen just read says that declines it.

    Never on a truncated read (`allow_truncated=False`): this is ocp
    choosing on its own, and it does not choose Esc on a dialog it could not
    read the whole of."""
    return _press_dialog(handle, declining_key, "Esc", allow_truncated=False)


# The exit dialog over background work, as Claude Code drew it (fixture
# EXIT_DIALOG, copied from a live screen): its highlighted first option stops
# the tasks and exits, and Enter confirms the highlighted one.
EXIT_OVER_WORK_TITLE = "background work is running"
EXIT_AND_STOP_LABEL = "exit and stop tasks"
ENTER = "enter"


def stopping_key(dialog: Dialog) -> tuple[str | None, str]:
    """(key, why not) - Enter, when this is the exit dialog over background work
    and the option highlighted is the one that stops the tasks and exits.

    The one place ocp chooses to stop a session's work, so it is as narrow as
    the parse allows: the title, the footer's own words, and the highlighted
    option's whole label must all be the measured ones. Anything else, however
    close, is declined like any dialog and the session stays."""
    if dialog.title.strip().lower() != EXIT_OVER_WORK_TITLE:
        return None, "it is not the exit dialog over background work"
    if "Enter to confirm" not in dialog.footer:
        return None, "its footer does not say Enter confirms"
    marked = [ln for ln in dialog.body if ln.startswith("\u276f")]
    m = _OPTION_LABEL.match(marked[0]) if len(marked) == 1 else None
    if not m or _norm_label(m[2]) != EXIT_AND_STOP_LABEL:
        return None, "its highlighted option is not 'Exit and stop tasks'"
    return ENTER, ""


def stop_work_and_exit(handle: str) -> tuple[str, str, str]:
    """Press Enter on the exit dialog over background work, when `stopping_key`
    allows; (outcome, title, why) as `decline_dialog` - including never on a
    truncated read, the same reason."""
    return _press_dialog(handle, stopping_key, "Enter", allow_truncated=False)


def accepts_tool(handle: str, tool: str | None) -> bool:
    """May a permission prompt for `tool` on this terminal be said yes to?"""
    return _driver_for(handle).accepts_tool(tool)


def accept_dialog(handle: str, tool: str | None) -> tuple[str, str, str]:
    """Press the vendor's one-time yes on its prompt to run `tool`, if - and
    only if - the screen just read is exactly that prompt (`accepting_key`);
    (outcome, title, why) as `decline_dialog`."""
    driver = _driver_for(handle)
    return _press_dialog(handle, lambda dialog: driver.accepting_key(dialog, tool), "Enter")


def _highlighted(dialog: Dialog) -> int | None:
    """The number of the one option marked as highlighted, or None."""
    marked = [ln for ln in dialog.body if ln.startswith("\u276f")]
    m = _OPTION_LABEL.match(marked[0]) if len(marked) == 1 else None
    return int(m[1]) if m else None


def answer_dialog(handle: str, choose) -> tuple[str, str, str]:
    """Press the number of the option `choose(vendor, dialog)` names on the
    dialog on this terminal's screen; (outcome, title, why) as `decline_dialog`.

    The number is read off the screen just read, never remembered. A vendor's
    folder-trust screen is never answered here, whatever `choose` says: that
    stays with the trust roots. A number over 9 is not one key and is refused.

    Whether a digit alone selects is not measured. When the digit leaves the same
    dialog open with the chosen option now the highlighted one and the footer
    saying Enter confirms, Enter is pressed once, on a fresh read; anything else
    still open is reported as such."""
    driver = _driver_for(handle)
    pressed: list[int] = []

    def pick(dialog: Dialog) -> tuple[str | None, str]:
        if driver.trust_screen(list(dialog.body)) is not None:
            return None, "a folder-trust screen is answered by the trust roots only"
        number = choose(driver.name, dialog)
        if number is None:
            return None, "no standing answer for this dialog"
        key, why = _digit_key(dialog, number)
        if key is not None:
            pressed.append(number)
        return key, why
    outcome, title, why = _press_dialog(handle, pick, "the option's number")
    if outcome != STILL_OPEN:
        return outcome, title, why
    replaced: list[bool] = []

    def confirm(dialog: Dialog) -> tuple[str | None, str]:
        if dialog.title != title:
            replaced.append(True)         # the ruled dialog is gone, another stands where it was
            return None, "another dialog is on screen now"
        if _highlighted(dialog) != pressed[0]:
            return None, "the chosen option is not the highlighted one"
        if "Enter to confirm" not in dialog.footer:
            return None, "its footer does not say Enter confirms"
        return ENTER, ""
    again = _press_dialog(handle, confirm, "Enter")
    if replaced:
        return CLEARED, title, ""         # as `_press_dialog` reads a replaced dialog
    if again[0] != NOT_PRESSED:
        return again
    # It closed between the two reads, or the screen is not one to press on.
    return (CLEARED, title, "") if again[2] == NO_DIALOG else (STILL_OPEN, title, why)


def confirmation_on_screen(handle: str):
    """The vendor's one-time permission prompt this terminal shows now, as a
    `Confirmation`, or None when there is none or it cannot be read."""
    dialog = dialog_on_screen(handle)
    return None if dialog is None else _driver_for(handle).confirmation(dialog)


def answer_confirmation(handle: str, yes: bool, seen: tuple[str, ...],
                        same_wait=lambda: True) -> tuple[str, str, str]:
    """Send a person's Yes or No to the permission prompt on this terminal;
    (outcome, title, why) as `decline_dialog`.

    Pressed only when the screen just read is a recognized prompt whose lines
    are exactly `seen`, the lines the person answered, and `same_wait()` - asked
    at that same moment - says it is still the very prompt they were shown:
    identical words are not identity, a second prompt for a repeat command
    reads the same as the first. The key is the number of the plain Yes or of
    the No - never "don't ask again" - through `answer_dialog`."""
    driver = _driver_for(handle)
    refused: list[str] = []

    def choose(vendor: str, dialog: Dialog) -> int | None:
        found = driver.confirmation(dialog)
        if found is None:
            refused.append("the screen is not a permission prompt ocp recognizes")
        elif found.lines != tuple(seen) or not same_wait():
            refused.append("the prompt is not the one that was answered")
        else:
            return found.yes if yes else found.no
        return None
    outcome, title, why = answer_dialog(handle, choose)
    return outcome, title, (refused[0] if refused and outcome == NOT_PRESSED else why)


PROMPT_PERMISSION, PROMPT_QUESTION, PROMPT_DIALOG = "permission", "question", "dialog"
PROMPT_NONE, PROMPT_UNREADABLE = "none", "unreadable"


def prompt_on_screen(handle: str) -> tuple[str, object]:
    """The ONE prompt this terminal shows now, read once: (PROMPT_PERMISSION,
    Confirmation), (PROMPT_QUESTION, Question), (PROMPT_DIALOG, Dialog),
    (PROMPT_NONE, None) or (PROMPT_UNREADABLE, None). A permission prompt
    outranks a question, which outranks a plain dialog - so a screen that more
    than one recognizer accepts is one prompt, not several. The folder-trust
    screen is not one of them: it has its own path (`trust`), and reads as none.
    An idle screen costs a single read."""
    term = _screen(handle)
    tail = term.get("tail") if term else None
    if not isinstance(tail, list) or term.get("source") != "screen":
        return PROMPT_UNREADABLE, None
    driver = _driver_for(handle)
    if driver.trust_screen(tail) is not None:
        return PROMPT_NONE, None
    _, _, dialog = _read_dialog(handle, term=term)
    confirmation = driver.confirmation(dialog) if dialog is not None else None
    if confirmation is not None:
        return PROMPT_PERMISSION, confirmation
    question = driver.ask_question(tail)
    if question is not None:
        return PROMPT_QUESTION, question
    if dialog is not None:
        return PROMPT_DIALOG, dialog
    return PROMPT_NONE, None


def question_on_screen(handle: str):
    """The AskUserQuestion prompt this terminal shows now, as a `Question`
    (T304), or None when there is none or it cannot be read. Read from the raw
    screen, not `dialog_on_screen`: this prompt's shape defeats `parse_dialog`
    (see `vendors.claude_ask`'s own docstring), so each vendor reads its `tail`
    itself, the way `trust_screen` does."""
    term = _screen(handle)
    tail = term.get("tail") if term else None
    if not isinstance(tail, list) or term.get("source") != "screen":
        return None
    return _driver_for(handle).ask_question(tail)


QUESTION_KEY_GAP_S = 0.1


def _send_question_keys(handle: str, keys: list[str]) -> tuple[bool, str]:
    for key in keys:
        ok, out = send(handle, "") if key == ENTER else send(handle, key, enter=False)
        if not ok:
            return False, f"a key could not be sent: {out[:120]}"
        time.sleep(QUESTION_KEY_GAP_S)
    return True, ""


def _question_cleared(handle: str, driver) -> bool:
    for _ in range(PRESS_SETTLE_READS):
        time.sleep(PRESS_SETTLE_S)
        after_term = _screen(handle)
        after_tail = after_term.get("tail") if after_term else None
        if not isinstance(after_tail, list) or after_term.get("source") != "screen":
            continue
        if driver.ask_question(after_tail) is None:
            return True
    return False


def _answer_question_toggle_and_other(handle: str, driver, found, choices: tuple[int, ...],
                                      text: str) -> tuple[str, str, str]:
    """A multiSelect question's boxes checked *and* Other typed together
    (T322/T336 follow-up, founder capture 2026-09-28): toggling a box moves
    the cursor too, so this sends only the toggles first, re-reads the real
    screen for where the cursor actually landed, and only then reaches for
    Other from there - never the whole guess in one blind sequence."""
    toggle_keys = driver.question_toggle_keys(found, choices)
    if toggle_keys is None:
        return NOT_PRESSED, found.header, "this answer is not one ocp sends"
    ok, why = _send_question_keys(handle, toggle_keys)
    if not ok:
        return NOT_PRESSED, found.header, why
    time.sleep(PRESS_SETTLE_S)
    term = _screen(handle)
    tail = term.get("tail") if term else None
    if not isinstance(tail, list) or term.get("source") != "screen":
        return STILL_OPEN, found.header, f"the screen could not be re-read after toggling {toggle_keys}"
    after_toggle = driver.ask_question(tail)
    if after_toggle is None:
        return STILL_OPEN, found.header, f"the question closed on its own after toggling {toggle_keys}"
    read_back = sorted(i for i, o in enumerate(after_toggle.options) if o.checked)
    # T336 diagnostics (temporary, founder #2013 - kept until this combo has a
    # few real captures behind it): the re-read that decides where Other is
    # reached from, spelled out so a log line shows it, not just the outcome.
    trail = (f"toggled {toggle_keys} -> checked {read_back}, cursor now {after_toggle.cursor}"
            f" (wanted {sorted(choices)})")
    if set(read_back) != set(choices):
        return STILL_OPEN, found.header, f"{trail} - not what was toggled, not sent further"
    other_keys = driver.question_other_after_toggle_keys(after_toggle, text)
    if other_keys is None:
        return NOT_PRESSED, found.header, f"{trail} - this answer is not one ocp sends"
    trail = f"{trail}, then Other's own keys {other_keys}"
    ok, why = _send_question_keys(handle, other_keys)
    if not ok:
        return NOT_PRESSED, found.header, f"{trail} - {why}"
    if _question_cleared(handle, driver):
        return CLEARED, found.header, trail
    return STILL_OPEN, found.header, f"{trail} - the question is still on screen after its answer"


def answer_question(handle: str, seen: tuple[str, ...], choices: tuple[int, ...] = (),
                    text: str | None = None) -> tuple[str, str, str]:
    """Send a person's answer to the AskUserQuestion prompt on this terminal;
    (outcome, title, why) as `decline_dialog`.

    Pressed only when the screen just read is a recognized prompt whose lines
    are exactly `seen`, the lines the person answered. Unlike a permission
    prompt's one key, an answer is several keys in a row (`answer_question_keys`),
    so the match is made once here, before the first - a key already sent
    cannot be un-sent on a later mismatch. Whether it landed is read off the
    question card itself going, never off the "Review your answers" screen a
    multiSelect answer passes through (vendors.claude_ask's own docstring).

    A multiSelect question's boxes checked and Other typed together goes
    through `_answer_question_toggle_and_other` instead, in two stages with a
    re-read between - `answer_question_keys` itself refuses that combination,
    since one blind sequence cannot know where toggling left the cursor."""
    driver = _driver_for(handle)
    term = _screen(handle)
    tail = term.get("tail") if term else None
    if not isinstance(tail, list) or term.get("source") != "screen":
        return NOT_PRESSED, "", "its screen could not be read"
    found = driver.ask_question(tail)
    if found is None:
        return NOT_PRESSED, "", NO_DIALOG
    if found.lines != tuple(seen):
        return NOT_PRESSED, found.header, "the question is not the one that was answered"
    if choices and text is not None and found.multi_select:
        return _answer_question_toggle_and_other(handle, driver, found, choices, text)
    keys = driver.answer_question_keys(found, choices, text)
    if keys is None:
        return NOT_PRESSED, found.header, "this answer is not one ocp sends"
    ok, why = _send_question_keys(handle, keys)
    if not ok:
        return NOT_PRESSED, found.header, why
    if _question_cleared(handle, driver):
        return CLEARED, found.header, ""
    return STILL_OPEN, found.header, "the question is still on screen after its answer"


# Claude Code's hint row under the composer counts what still runs in the
# background as "<n> <noun>" segments between its mode and its key hints, e.g.
# "⏵⏵ auto mode on · 2 monitors · ← for agents · ↓ to manage" (measured live
# 2026-09-24). "↓ to manage" is its hint for that list and is drawn only while
# there is one.
_BG_COUNT = re.compile(r"^\d+\s+[A-Za-z][A-Za-z ]*$")
_BG_HINT = "to manage"


# The rows `background_work` last read for each terminal, so a caller that wants to keep a sample of
# the screen it was read from does not read it twice (T592).
_last_tail: dict[str, list[str]] = {}


def last_tail(handle: str) -> list[str] | None:
    """The screen rows `background_work` last read for this terminal, or None."""
    return _last_tail.get(handle)


def footer_background(tail: list) -> str | None:
    """`background_work`'s reading of rows already read: what the hint row under the composer
    counts, "" when it counts nothing, None when no composer is drawn."""
    box = _composer_box(tail)
    if box is None:
        return None
    for row in tail[box[1] + 1:]:
        row = str(row)
        if _BG_HINT not in row:
            continue
        parts = [p.strip() for p in row.split("·")]
        found = [p for p in parts if _BG_COUNT.match(p)]
        return ", ".join(found) or "background tasks"
    return ""


def background_work(handle: str) -> str | None:
    """What the session itself says is still running behind its prompt - "2
    monitors", "1 shell" - or "" when it says nothing, or None when its screen
    could not be read (no composer to read the hint row under).

    Read from the hint row under the composer, the session's own count, never
    from a timer or from what ocp last typed."""
    term = _screen(handle)
    tail = term.get("tail") if term else None
    if not isinstance(tail, list) or term.get("source") != "screen":
        return None
    _last_tail[handle] = [str(r) for r in tail]
    return footer_background(tail)


def idle_with_background(handle: str) -> bool:
    """Does the screen show a session idle at its prompt with work running behind it (T458)?

    Orca's own idle answer reads such a session as mid-turn, and a task is never typed at it.
    The screen is the second witness, and only on the busy side's absence: the driver says
    what a turn in progress draws."""
    term = _screen(handle)
    tail = term.get("tail") if term else None
    if not isinstance(tail, list) or term.get("source") != "screen":
        return False
    return _driver_for(handle).idle_with_background(tail)


def usage_limit_open(handle: str) -> bool:
    """Is the dialog on this terminal's screen the usage-limit one, the only
    dialog that waiting does not clear and a person is not answering?"""
    term = _screen(handle)
    tail = term.get("tail") if term else None
    if not isinstance(tail, list) or term.get("source") != "screen":
        return False
    footer = _footer_row(tail)
    dialog = parse_dialog(tail, footer) if footer is not None else None
    return bool(dialog and _usage_limit_dialog(dialog))


def modal_open(handle: str) -> bool:
    """Is a modal on the agent's screen, where typed text would answer it?"""
    blocked = screen_blocker(handle)
    return bool(blocked and blocked.kind in (MODAL, BACKGROUNDED, UNREADABLE))


def typeable_now(handle: str) -> bool:
    """Idle and nothing on screen that a typed line would answer."""
    # Nothing at all is asked with one read, not one per kind, so a dialog
    # that opens between two separate reads cannot be read as neither.
    with driver_scope():
        return (idle_now(handle) or idle_with_background(handle)) and screen_blocker(handle) is None


def clear_context(handle: str, wait: bool = True,
                  timeout_ms: int = IDLE_TIMEOUT_MS) -> tuple[bool, str]:
    """Return the session's context without destroying the agent.

    Waits for the agent to finish what it is doing first: clearing mid-turn
    would throw away work the handoff has not recorded yet.
    """
    if not alive(handle):
        return False, f"terminal {handle} is not alive"
    with driver_scope():
        if wait and not wait_idle(handle, timeout_ms):
            return False, "agent is mid-turn; not clearing"
        blocked = screen_blocker(handle)          # one read, not modal_open() twice
        cmd = clear_command(handle)
    if blocked:
        return False, f"{blocked.reason()}; not typing {cmd} at it"
    ok, out = send(handle, cmd)
    return ok, "context cleared" if ok else f"could not send {cmd}: {out[:200]}"


def close_session(handle: str, wait: bool = True,
                  timeout_ms: int = IDLE_TIMEOUT_MS) -> tuple[bool, str]:
    """End the agent's session and leave its terminal standing.

    Orca runs our launch command inside a login shell, so the agent's process
    is the shell's child: when it exits the shell survives and the tab, the
    worktree and the cwd stay exactly as they were. Measured on a live claude
    agent: 401MB returned to the machine, terminal still running, prompt in
    the same worktree.

    Same guard as clearing - ending a session mid-turn throws away work that
    nothing has recorded yet - and nothing is typed at a modal, where `/exit`
    and Enter answer the question instead of ending anything.

    "Ok" means the command was typed. Whether the process ended is the
    caller's to confirm from the process table.
    """
    if not alive(handle):
        return False, f"terminal {handle} is not alive"
    with driver_scope():
        if wait and not wait_idle(handle, timeout_ms):
            return False, "agent is mid-turn; not closing"
        blocked = screen_blocker(handle)          # one read, not modal_open() twice
        cmd = _driver_for(handle).exit_command()
    if blocked:
        return False, f"{blocked.reason()}; not typing {cmd} at it"
    ok, out = send(handle, cmd)
    return ok, f"{cmd} typed" if ok else f"could not send {cmd}: {out[:200]}"


# Between a launch line and the Enter that runs it.
LAUNCH_ENTER_DELAY_S = 0.5


def _terminal_note(handle: str) -> str:
    """What a launch that started nothing should say about the terminal it was typed into: that it is
    gone, or that it is alive and what its last rows show. One line and cut short - it is text an
    agent's shell drew."""
    if not alive(handle):
        return "; the terminal is gone"
    term = _screen(handle)
    tail = term.get("tail") if term else None
    if not isinstance(tail, list) or term.get("source") != "screen":
        return "; the terminal is alive but its screen could not be read"
    rows = [textsafe.oneline(str(r).strip()) for r in tail if str(r).strip()][-3:]
    return f"; the terminal is alive (screen: {' | '.join(rows)[:240]})" if rows else "; the terminal is alive"


def relaunch(handle: str, command: str, timeout_ms: int = IDLE_TIMEOUT_MS,
             started=None) -> tuple[bool, str]:
    """Start an agent again in the shell its session left behind.

    The command is ours, so the account comes back with it - which is why this
    does not depend on anything preserving the launch environment for us.

    `started` is asked, before the wait, whether the command actually started
    something: sending only says we typed it, and `ocp launch` refuses over the
    5h limit and exits when the account is not logged in. It comes first
    because the wait below costs its whole timeout when there is no TUI, and a
    launch that started nothing is exactly that case.
    """
    if not alive(handle):
        return False, f"terminal {handle} is not alive"
    if session_on_screen(handle):
        # Whatever the process table says, a session is drawn there: the line would reach it as a
        # prompt, and a session that obeyed it would start a second copy of itself.
        return False, "a session is on the terminal's screen; not typing a launch line at it"
    # The line and its Enter go as two sends, in case an Enter that arrives with the line is swallowed
    # by a shell taking it as a paste (T663: a hypothesis, not an observed cause).
    ok, out = send(handle, command, enter=False)
    if ok:
        time.sleep(LAUNCH_ENTER_DELAY_S)
        ok, out = send(handle, "")
    if not ok:
        return False, f"could not send the launch command: {out[:200]}"
    if started is not None and not started():
        return False, f"the launch command was typed but no session started{_terminal_note(handle)}"
    # Typing into a TUI that is still booting loses the opening characters, so
    # nothing may be sent to the agent until it has finished starting.
    wait_idle(handle, timeout_ms)
    return True, "relaunched"


def resume_with(handle: str, text: str) -> tuple[bool, str]:
    """Wake a cleared agent by giving it back its own handoff."""
    if not alive(handle):
        return False, f"terminal {handle} is not alive"
    ok, out = send(handle, text)
    return ok, "handoff injected" if ok else f"could not send the handoff: {out[:200]}"
