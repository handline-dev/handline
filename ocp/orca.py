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

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

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
    return shutil.which("orca") is not None


def terminal_handle() -> str | None:
    """The terminal we are running inside, if Orca launched us."""
    return os.environ.get("ORCA_TERMINAL_HANDLE")


def _run(args: list[str], timeout: int = 180) -> tuple[bool, str]:
    try:
        r = subprocess.run(["orca", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout or r.stderr).strip()


def wait_idle(handle: str, timeout_ms: int = IDLE_TIMEOUT_MS) -> bool:
    """Whether the agent is done working.

    `ok` only says the wait command ran; `result.wait.satisfied` is the answer.
    Reading `ok` alone treats a timeout as "idle" and clears mid-turn.
    """
    ok, out = _run(
        ["terminal", "wait", "--terminal", handle, "--for", "tui-idle",
         "--timeout-ms", str(timeout_ms), "--json"],
        timeout=timeout_ms // 1000 + 30,
    )
    if not ok:
        return False
    try:
        return bool(json.loads(out)["result"]["wait"]["satisfied"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return False


def send(handle: str, text: str, enter: bool = True) -> tuple[bool, str]:
    args = ["terminal", "send", "--terminal", handle, "--text", text]
    if enter:
        args.append("--enter")
    return _run(args)


def _result(out: str, *path: str):
    """Dig one value out of an `--json` reply, or None."""
    try:
        node = json.loads(out).get("result", {})
    except (json.JSONDecodeError, TypeError):
        return None
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def run_create(objective: str) -> str | None:
    """Start an orchestration Run - the card a team's work hangs under.

    It also *binds* the calling terminal to that Run, which is why this is
    called once and remembered: calling it again makes a second card with the
    same name and moves the binding to it, and a team split across two cards
    is a tree that draws half of itself.
    """
    ok, out = _run(["orchestration", "run-create", "--objective", objective, "--json"])
    if not ok:
        return None
    return _result(out, "run", "id") or _result(out, "id")


def run_current() -> str | None:
    """The Run this terminal is already bound to, if any."""
    ok, out = _run(["orchestration", "run-current", "--json"])
    return _result(out, "run", "id") if ok else None


def run_exists(run: str) -> bool:
    """Is this Run still there? `--id`, not `--run`: the flag differs per verb,
    and reading the wrong one as "gone" made a new Run for every agent."""
    ok, out = _run(["orchestration", "run-show", "--id", run, "--json"])
    return ok and bool(_result(out, "run", "id"))


def run_use(run: str) -> bool:
    """Bind this terminal to an existing Run, so tasks land in the team's card."""
    ok, _out = _run(["orchestration", "run-use", "--id", run, "--json"])
    return ok


def task_create(spec: str, title: str, display_name: str, run: str,
                parent: str | None = None) -> str | None:
    """A task in the Run, optionally under another - this is the tree Orca draws."""
    args = ["orchestration", "task-create", "--spec", spec, "--task-title", title,
            "--display-name", display_name, "--run", run, "--json"]
    if parent:
        args += ["--parent", parent]
    ok, out = _run(args)
    if not ok:
        return None
    return _result(out, "task", "id") or _result(out, "id")


def dispatch(task: str, handle: str, run: str) -> str | None:
    """Bind a task to a terminal, which is what makes it a worker in the card.

    No `--inject`: the terminal is already running `ocp launch`, and the spec
    is metadata for the card rather than an instruction for the agent. Orca
    injecting it too would deliver the same thing twice.
    """
    ok, out = _run(["orchestration", "dispatch", "--task", task, "--to", handle,
                    "--run", run, "--json"])
    if not ok:
        return None
    return _result(out, "dispatch", "id") or _result(out, "dispatchId") or task


def focus(handle: str) -> tuple[bool, str]:
    """Bring this terminal's tab to the front in the Orca window.

    The one thing the screen does that is not about state: the reader stops
    deciding and starts working, in the agent's own session, with everything it
    can see. Nothing is typed and nothing is read - which is why it is safe on
    an agent that is mid-turn, where every other key here is not.
    """
    return _run(["terminal", "switch", "--terminal", handle], timeout=20)


def live_terminals() -> set[str] | None:
    """Every handle Orca currently has, or None when it could not say.

    None is not an empty set. Reading "I could not ask" as "there are none"
    would tear down a healthy team the first time Orca was slow to answer.
    """
    ok, out = _run(["terminal", "list", "--json"])
    if not ok:
        return None
    try:
        terms = json.loads(out)["result"]["terminals"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return {t["handle"] for t in terms if isinstance(t, dict) and t.get("handle")}


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


def _screen(handle: str) -> dict | None:
    """The rendered screen, or None when it cannot be read."""
    ok, out = _run(["terminal", "read", "--terminal", handle, "--screen",
                    "--limit", "40", "--json"], timeout=30)
    term = _result(out, "terminal") if ok else None
    return term if isinstance(term, dict) else None


# Why a line typed now would not land, named.
MODAL, UNSENT, UNREADABLE = "modal", "unsent", "unreadable"


@dataclass(frozen=True)
class Blocker:
    kind: str
    # What the screen itself says: the dialog's title and options, or the start
    # of the text left in the input. Quoted from the screen, never classified.
    detail: str = ""
    # The dialog's own title, raw from parse_dialog - not reconstructed from
    # `detail`, which is a rendering (quoted, joined, truncated) that a title
    # with a quote in it or over DETAIL_CHARS long does not round-trip through.
    # Empty for anything but MODAL, or a MODAL parse_dialog itself refuses.
    title: str = ""

    def reason(self) -> str:
        if self.kind == MODAL:
            return f'a dialog is open on its screen: {self.detail}'
        if self.kind == UNSENT:
            return f'text is left unsent in its input: "{self.detail}"'
        return "its screen could not be read"


# A dialog's panel opens with a rule of these (measured on Claude Code's exit
# confirmation); the title is the first line under it.
_RULE = re.compile(r"^\u2594{8,}$")
_OPTION = re.compile(r"^(?:\u276f\s*)?\d+\.\s+\S")
DETAIL_CHARS = 200

# What a terminal would obey in text quoted off a screen: an OSC or CSI
# sequence, a lone ESC, C0, DEL, C1. An agent can paint its own screen, so a
# dialog's title and options are exactly the hostile-text case
# buddyron/shared-sanitiser's textsafe.oneline() exists for - this IS a second
# copy of its rule, not merged yet only because that branch is not on main.
# Applied at the source - the earliest point this text leaves a screen, before
# it reaches a log line or a request body neither of us sees the sink of.
# Nothing here fails if the two drift before shared-sanitiser lands; once it
# does, this becomes a call to textsafe.oneline() and these two regexes go.
_OSC = re.compile(r"\033\][^\a\033]*(?:\a|\033\\)?")
_CSI = re.compile(r"\033\[[0-?]*[ -/]*[@-~]")


def _visible(s: str) -> str:
    """`s` as one line with nothing a terminal would obey."""
    s = _CSI.sub("", _OSC.sub("", s))
    return "".join(c if c.isprintable() else " " for c in " ".join(s.splitlines()))


def describe_modal(tail: list[str], footer: int) -> str:
    """The dialog above row `footer` in its own words: its title, then its
    numbered options. Where no rule marks where the dialog begins, the last
    three lines above the footer, verbatim - a quotation, so a layout this has
    not seen still says something true."""
    lines = [_visible(str(x).strip()) for x in tail[:footer]]
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


def parse_dialog(tail: list[str], footer: int) -> Dialog | None:
    """The dialog above row `footer` as data, or None when any part of it is
    not there to read: no rule opening the panel, no title, or fewer than two
    options numbered 1, 2, ... in order. What cannot be parsed is not guessed."""
    lines = [_visible(str(x).strip()) for x in tail[:footer]]
    top = next((i for i in range(len(lines) - 1, -1, -1) if _RULE.match(lines[i])), None)
    if top is None:
        return None
    body = [ln for ln in lines[top + 1:] if ln]
    if not body or _OPTION_LABEL.match(body[0]):
        return None
    options = [(int(m[1]), m[2].strip()) for m in map(_OPTION_LABEL.match, body[1:]) if m]
    if len(options) < 2 or [n for n, _ in options] != list(range(1, len(options) + 1)):
        return None
    return Dialog(body[0], tuple(options), _visible(str(tail[footer]).strip()), tuple(body))


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


def _inert(label: str) -> bool:
    return re.sub(r"\s*\(esc\)$", "", label.strip().lower()).rstrip(".!") in INERT_LABELS


# _ESC_MARK (case-insensitive, a trailing space allowed) and the strip inside
# _inert (case-sensitive after its own .lower(), no trailing space allowed)
# are deliberately different patterns, not a drifted duplicate: _ESC_MARK only
# ever has to notice the mark is there at all, on raw screen text; _inert only
# ever sees a mark it has already found, on text it has already lowercased and
# stripped itself. They agree because each does the normalising its own job
# needs, not because they are meant to be the same regex.


def declining_key(dialog: Dialog) -> tuple[str | None, str]:
    """(key, why not) - Escape, when the dialog itself says Esc cancels it and
    offers an option that only declines; otherwise no key and the reason.

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
    decline", which is the evidence available when nothing names Esc directly.
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
    if not any(_inert(label) for _, label in dialog.options):
        return None, "none of its options only declines"
    return ESCAPE, ""


def screen_blocker(handle: str) -> Blocker | None:
    """What on the screen a typed line would answer or merge into, from one read.

    A dialog draws its footer on the bottom rows; above them is the transcript,
    which may quote those words without anything being asked. Text in the input
    box is what Orca reports as `draft`, only while it is non-empty, and keeps
    out of `tail`. Fail-closed: a screen that cannot be read blocks, because the
    cost of a wrong no is an answer given to a question nobody asked, or a
    prompt submitted with someone's half-written text in it.
    """
    term = _screen(handle)
    tail = term.get("tail") if term else None
    # Any source but the screen is the accumulated stream, whose history is the
    # conversation, not what is drawn now.
    if not isinstance(tail, list) or term.get("source") != "screen":
        return Blocker(UNREADABLE)
    footer = _footer_row(tail)
    if footer is not None:
        dialog = parse_dialog(tail, footer)
        return Blocker(MODAL, describe_modal(tail, footer), title=dialog.title if dialog else "")
    draft = term.get("draft")
    if draft:
        return Blocker(UNSENT, " ".join(str(draft).split())[:DETAIL_CHARS])
    return None


CLEARED, STILL_OPEN, NOT_PRESSED = "cleared", "still-open", "not-pressed"
# How long a dialog is given to go after Esc, read again each time.
PRESS_SETTLE_S, PRESS_SETTLE_READS = 0.5, 4


def decline_dialog(handle: str) -> tuple[str, str, str]:
    """Press Esc on the dialog on this terminal's screen, if - and only if -
    the screen just read says that declines it. (outcome, dialog title, why).

    The screen is read here, immediately before the key, and read again after
    it: what is pressed is what is on screen now, never what an earlier read
    said. Nothing is pressed on a screen that cannot be read, is not a
    dialog, or has a title or options that do not parse; a key that is sent
    and does not close the dialog is reported as still open, not repeated.
    """
    term = _screen(handle)
    tail = term.get("tail") if term else None
    if not isinstance(tail, list) or term.get("source") != "screen":
        return NOT_PRESSED, "", "its screen could not be read"
    footer = _footer_row(tail)
    if footer is None:
        return NOT_PRESSED, "", "no dialog is open"
    if term.get("draft"):
        # Esc is not inert at a Claude Code prompt - it clears the input box.
        # "The worst a press does is change nothing" only holds where landing
        # anywhere but the dialog is impossible; a person's own unsent text
        # sitting in the same read is the one case it is not.
        return NOT_PRESSED, "", "text is left unsent in its input; not pressing over it"
    dialog = parse_dialog(tail, footer)
    if dialog is None:
        return NOT_PRESSED, "", "its title or options could not be parsed"
    key, why = declining_key(dialog)
    if key is None:
        return NOT_PRESSED, dialog.title, why
    ok, out = send(handle, key, enter=False)
    if not ok:
        return NOT_PRESSED, dialog.title, f"the key could not be sent: {out[:120]}"
    for _ in range(PRESS_SETTLE_READS):
        time.sleep(PRESS_SETTLE_S)
        after_term = _screen(handle)
        after_tail = after_term.get("tail") if after_term else None
        if not isinstance(after_tail, list) or after_term.get("source") != "screen":
            continue  # not evidence either way - the same screen fails closed everywhere else
        after_footer = _footer_row(after_tail)
        if after_footer is None:
            return CLEARED, dialog.title, ""
        after_dialog = parse_dialog(after_tail, after_footer)
        if after_dialog is not None and after_dialog.title != dialog.title:
            # Not the dialog Esc was pressed on - that one is gone, whatever
            # this new one is; still-open must never be counted against a
            # title that is not what is actually still on screen, since that
            # is what a loop is measured by.
            return CLEARED, dialog.title, ""
    return STILL_OPEN, dialog.title, "the dialog is still on screen after Esc"


def modal_open(handle: str) -> bool:
    """Is a modal on the agent's screen, where typed text would answer it?"""
    blocked = screen_blocker(handle)
    return bool(blocked and blocked.kind in (MODAL, UNREADABLE))


def unsent_text(handle: str) -> bool:
    """Is there text in the input box that nobody has submitted?

    Anything typed now lands in the middle of it and the Enter submits the two
    as one prompt.
    """
    blocked = screen_blocker(handle)
    return bool(blocked and blocked.kind in (UNSENT, UNREADABLE))


def typeable_now(handle: str) -> bool:
    """Idle, nothing on screen that a typed line would answer, and nothing
    already typed that it would merge into."""
    # screen_blocker's own kinds are exactly MODAL, UNSENT and UNREADABLE, so
    # not-modal-and-not-unsent (each fail-closed on UNREADABLE too) is the
    # same question as "nothing at all" - asked with one read, not two, so a
    # dialog that opens between two separate reads cannot be read as neither.
    return idle_now(handle) and screen_blocker(handle) is None


def clear_context(handle: str, wait: bool = True,
                  timeout_ms: int = IDLE_TIMEOUT_MS) -> tuple[bool, str]:
    """Return the session's context without destroying the agent.

    Waits for the agent to finish what it is doing first: clearing mid-turn
    would throw away work the handoff has not recorded yet.
    """
    if not alive(handle):
        return False, f"terminal {handle} is not alive"
    if wait and not wait_idle(handle, timeout_ms):
        return False, "agent is mid-turn; not clearing"
    blocked = screen_blocker(handle)          # one read, not modal_open() then unsent_text()
    if blocked and blocked.kind in (MODAL, UNREADABLE):
        return False, "a modal is open on its screen; not typing /clear at it"
    if blocked and blocked.kind == UNSENT:
        return False, "text is left unsent in its input; not typing /clear into it"
    ok, out = send(handle, "/clear")
    return ok, "context cleared" if ok else f"could not send /clear: {out[:200]}"


def close_session(handle: str, wait: bool = True,
                  timeout_ms: int = IDLE_TIMEOUT_MS) -> tuple[bool, str]:
    """End the agent's session and leave its terminal standing.

    Orca runs our launch command inside a login shell, so claude is the shell's
    child: when it exits the shell survives and the tab, the worktree and the
    cwd stay exactly as they were. Measured on a live agent: 401MB returned to
    the machine, terminal still running, prompt in the same worktree.

    Same guard as clearing - ending a session mid-turn throws away work that
    nothing has recorded yet - and nothing is typed at a modal, where `/exit`
    and Enter answer the question instead of ending anything.

    "Ok" means the command was typed. Whether the process ended is the
    caller's to confirm from the process table.
    """
    if not alive(handle):
        return False, f"terminal {handle} is not alive"
    if wait and not wait_idle(handle, timeout_ms):
        return False, "agent is mid-turn; not closing"
    blocked = screen_blocker(handle)          # one read, not modal_open() then unsent_text()
    if blocked and blocked.kind in (MODAL, UNREADABLE):
        return False, "a modal is open on its screen; not typing /exit at it"
    if blocked and blocked.kind == UNSENT:
        return False, "text is left unsent in its input; not typing /exit into it"
    ok, out = send(handle, "/exit")
    return ok, "/exit typed" if ok else f"could not send /exit: {out[:200]}"


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
    ok, out = send(handle, command)
    if not ok:
        return False, f"could not send the launch command: {out[:200]}"
    if started is not None and not started():
        return False, "the launch command was typed but no session started"
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
