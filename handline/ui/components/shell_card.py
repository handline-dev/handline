"""A `!command` the founder ran from the chat box, drawn as a card: the command, how it ended, and what it
printed. The card only shows a `ShellRun` (actions/shell.py); stopping and sending are its buttons."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Static

from ...actions.shell import ShellRun
from ...infra import textsafe
from .dialog import Dialog

SHOWN = 200         # the last lines the card holds; the rest is behind "show all"


def head(run: ShellRun) -> Text:
    out = Text(f"$ {textsafe.oneline(run.command)}", style="bold")
    code = run.code
    if code is None:
        out.append(f"   running {int(run.seconds())}s", style="yellow")
    elif run.stopped:
        out.append(f"   stopped · {run.seconds():.1f}s", style="red")
    else:
        out.append(f"   exit {code} · {run.seconds():.1f}s", style="green" if code == 0 else "bold red")
    return out


def body(lines: list[str], dropped: int) -> tuple[Text, int]:
    """The tail of the output as text, and how many lines are left out of it."""
    tail = lines[-SHOWN:]
    out = Text("\n".join(l for ln in tail for l in textsafe.drawable(ln)) if tail else "(no output)",
               style="" if tail else "dim")
    return out, dropped + len(lines) - len(tail)


class OutputModal(Dialog):
    """Everything a command printed, scrollable; Esc closes."""

    FOCUS = "#shell_all"
    DEFAULT_CSS = """
    OutputModal #dialog { width: 90%; height: 85%; }
    OutputModal #shell_title { text-style: bold; margin-bottom: 1; }
    OutputModal #shell_all { height: 1fr; }
    """

    def __init__(self, title: str, lines: list[str], dropped: int) -> None:
        super().__init__()
        self.title_text, self.lines, self.dropped = title, lines, dropped

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(self.title_text), id="shell_title")
            with VerticalScroll(id="shell_all"):
                if self.dropped:
                    yield Static(Text(f"... {self.dropped} earlier lines were let go", style="dim"))
                yield Static(Text("\n".join(l for ln in self.lines for l in textsafe.drawable(ln))))


class ShellCard(Vertical):
    """One command. Stop while it runs; Show all when the card holds less than it printed; Send to agent
    once it has something to say (posts `ShellCard.Send`, by itself when a command ends that was not stopped)."""

    class Send(Message):
        def __init__(self, run: ShellRun) -> None:
            super().__init__()
            self.run = run

    DEFAULT_CSS = """
    ShellCard { height: auto; border: round $primary; padding: 0 1; margin: 1 0; }
    ShellCard.running { border: round $warning; }
    ShellCard.failed { border: round $error; }
    ShellCard .sh_head { height: auto; }
    ShellCard .sh_out { height: auto; max-height: 12; margin-top: 1; }
    ShellCard .sh_cut { height: auto; color: $text-muted; }
    ShellCard .sh_buttons { height: auto; margin-top: 1; }
    ShellCard .sh_buttons Button { margin-right: 2; }
    """

    def __init__(self, run: ShellRun, **kw) -> None:
        super().__init__(**kw)
        self.run = run
        self._head, self._out, self._cut = Static(classes="sh_head"), Static(), Static(classes="sh_cut")
        self._stop = Button("Stop", variant="error", classes="sh_stop", compact=True)
        self._all = Button("Show all", classes="sh_all", compact=True)
        self._send = Button("Send to agent", classes="sh_send", compact=True)
        self._final = False

    def compose(self) -> ComposeResult:
        yield self._head
        yield self._cut
        with VerticalScroll(classes="sh_out"):
            yield self._out
        yield Horizontal(self._stop, self._all, self._send, classes="sh_buttons")

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.25, self.show)    # before the first show: a run that is over stops it there
        self.show()

    def show(self) -> None:
        """Redraw from the run; once it has ended and been drawn that last time, stop redrawing."""
        if self._final:
            return
        lines, dropped, code = self.run.snapshot()
        text, left = body(lines, dropped)
        self._head.update(head(self.run))
        self._out.update(text)
        self._cut.update(Text(f"... {left} earlier lines left out" if left else ""))
        self._cut.display = bool(left)
        self._all.display = bool(left)
        self._stop.display = code is None
        self._send.display = code is not None
        self.set_class(code is None, "running")
        self.set_class(code not in (None, 0), "failed")
        if code is not None:
            self._final = True
            self._timer.stop()
            self.query_one(".sh_out").scroll_end(animate=False)
            if not self.run.sent and not self.run.stopped:      # the result goes to the agent on its own, once
                self.post_message(self.Send(self.run))

    def stop(self) -> None:
        self.run.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button is self._stop:
            self.stop()
        elif event.button is self._all:
            lines, dropped, _ = self.run.snapshot()
            self.app.push_screen(OutputModal(f"$ {textsafe.oneline(self.run.command)}", lines, dropped))
        else:
            self.post_message(self.Send(self.run))
