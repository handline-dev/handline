"""The key guide every screen wears at its foot: the keys that matter most that fit
on one line, then `?`, which opens the full list in a box. A screen gives its keys
with a rank (1 the ones to keep when the line is short, 3 the ones dropped first);
what fits, and what the box says, is decided here."""
from __future__ import annotations

from dataclasses import dataclass

from rich.cells import cell_len
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from .close_bar import CloseCorner

SEP = " · "
HELP_KEY = "?"
HELP_LABEL = "keys"


@dataclass(frozen=True)
class Key:
    key: str
    label: str
    rank: int = 2
    help: str = ""


def fit(keys: list[Key], width: int) -> Text:
    """The guide on one line of `width` cells: keys in the order given, the
    lowest-ranked left out until it fits, ending in `?`."""
    tail = Text.assemble((HELP_KEY, "bold"), (f" {HELP_LABEL}", "dim"))
    room = width - tail.cell_len

    def w(i: int) -> int:
        return cell_len(keys[i].key) + 1 + cell_len(keys[i].label) + len(SEP)

    chosen = set(range(len(keys)))
    for i in sorted(chosen, key=lambda i: (-keys[i].rank, -i)):
        if sum(w(j) for j in chosen) <= room:
            break
        chosen.discard(i)
    out = Text(no_wrap=True, overflow="ellipsis")
    for i in sorted(chosen):
        out.append(keys[i].key, "bold")
        out.append(f" {keys[i].label}", "dim")
        out.append(SEP, "dim")
    out.append_text(tail)
    return out


class KeyGuide(Static):
    """One line at the foot; `keys` is what the screen offers now."""

    DEFAULT_CSS = "KeyGuide { height: 1; }"

    def __init__(self, keys: list[Key] | None = None, **kw) -> None:
        super().__init__(**kw)
        self.keys = keys or []

    def set_keys(self, keys: list[Key]) -> None:
        self.keys = keys
        self.update(fit(self.keys, self.size.width))

    def on_resize(self) -> None:
        self.update(fit(self.keys, self.size.width))


class KeyHelp(CloseCorner, ModalScreen[None]):
    """Every key of a screen, with what it does."""

    BINDINGS = [Binding("escape", "dismiss", "close"), Binding("question_mark", "dismiss", "close")]
    DEFAULT_CSS = """
    KeyHelp { align: center middle; background: $background 60%; }
    KeyHelp #dialog { width: 64; max-width: 90%; height: auto; max-height: 90%;
                      padding: 1 2; border: thick $primary; background: $panel; }
    KeyHelp #hint { margin-top: 1; color: $text-muted; }
    """

    def on_mount(self) -> None:
        self.mark_close_corner()

    def close(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.close()

    def __init__(self, title: str, keys: list[Key]) -> None:
        super().__init__()
        self.title_text, self.keys = title, keys

    def compose(self) -> ComposeResult:
        body = Text()
        width = max((cell_len(k.key) for k in self.keys), default=0)
        for k in self.keys:
            body.append(k.key.ljust(width + 2), "bold")
            body.append((k.help or k.label) + "\n")
        with Vertical(id="dialog"):
            yield Static(Text(self.title_text, style="bold"))
            yield Static(body, id="keys")
            yield Static("esc or ? closes", id="hint")
