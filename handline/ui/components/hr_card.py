"""An HR request as a card the founder can answer where it is shown: in the chat, and on the HR page.
`pending()` and `decide()` are the only way in; the card itself knows nothing of where requests live."""
from __future__ import annotations

from typing import Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Static

from ... import actions
from ...infra import textsafe, timefmt
from ...team import hr

# state -> (word, style). `approved` is an approve whose change did not go through: it can be approved
# again (a retry) or rejected; `proposed` waits for the first answer.
STATES = {"proposed": ("waiting for you", "bold yellow"), "approved": ("approved, apply failed", "bold red"),
          "applied": ("approved and applied", "bold green"), "rejected": ("rejected", "bold red"),
          "withdrawn": ("withdrawn", "dim")}


def pending(since: int = 0) -> list[dict]:
    """The HR requests to draw, oldest first: the ones waiting on the founder (or approved but not
    carried out), then the ones settled at or after `since`. A change the founder made directly was
    never put to them, so it is no request. The one place the chat and the HR page read them from."""
    cards = [c for c in hr.pending() + hr.decided(since) if c.get("requested_by") != actions.HUMAN]
    return sorted(cards, key=lambda c: (c["ts"], c["id"]))


def decide(p: dict, verdict: str, note: str = "") -> tuple[bool, str]:
    """Approve (`verdict` 'approved') or reject ('rejected') request `p` as the founder; an approve is
    carried out at once, and is for the text the card showed (`subject`)."""
    return hr.decide(p["id"], verdict, actions.HUMAN, note, p["subject"])


def head(p: dict) -> Text:
    word, style = STATES.get(p["state"], (p["state"], "dim"))
    out = Text(f"HR · {p['kind']} {textsafe.oneline(str(p['agent']))}", style="bold")
    out.append(f"   {word}", style=style)
    out.append(f"   from {textsafe.oneline(str(p.get('requested_by') or '?'))}, {timefmt.ago_short(p['ts'])} ago",
               style="dim")
    return out


def body(p: dict) -> Text:
    """What is asked and why; every value came from an agent, so each is made safe to draw."""
    out = Text()
    if p.get("text"):
        said = [ln for ln in textsafe.drawable(str(p["text"])) if not ln.startswith("Approve or reject:")]
        out.append("\n".join(said).strip() + "\n")
    out.append("why  ", style="dim")
    out.append("\n".join(textsafe.drawable(str(p.get("why") or ""))))
    if p["state"] == "approved" and p.get("error"):
        out.append("\nerror ", style="red")
        out.append("\n".join(textsafe.drawable(str(p["error"])))[:400])
    return out


class HrCard(Vertical):
    """One HR request: what, from whom, why, and Approve / Reject while it waits. Reject asks for a
    reason in a line under the buttons. A decision is made through `decide`, then the card redraws
    from the request it returns state for (`show`) and posts `HrCard.Decided`."""

    class Decided(Message):
        def __init__(self, card: "HrCard", ok: bool, said: str) -> None:
            super().__init__()
            self.card, self.ok, self.said = card, ok, said

    DEFAULT_CSS = """
    HrCard { height: auto; border: round $primary; padding: 0 1; margin: 1 0; }
    HrCard .hr_head { height: auto; }
    HrCard .hr_body { height: auto; margin-top: 1; }
    HrCard .hr_buttons { height: auto; margin-top: 1; }
    HrCard .hr_buttons Button { margin-right: 2; }
    HrCard .hr_why { display: none; margin-top: 1; }
    HrCard.rejecting .hr_why { display: block; }
    HrCard.proposed { border: round $warning; }
    HrCard.approved, HrCard.applied { border: round $success; }
    HrCard.rejected { border: round $error; }
    HrCard.withdrawn { border: round $panel; }
    """

    def __init__(self, p: dict, decide: Callable[[dict, str, str], tuple[bool, str]] = decide, **kw) -> None:
        super().__init__(**kw)
        self.p, self._decide = p, decide
        self._head, self._body = Static(classes="hr_head"), Static(classes="hr_body")
        self._approve = Button("Approve", variant="success", classes="hr_approve", compact=True)
        self._reject = Button("Reject", variant="error", classes="hr_reject", compact=True)
        self._why = Input(placeholder="why - Enter rejects, Esc cancels", classes="hr_why")

    def compose(self) -> ComposeResult:
        yield self._head
        yield self._body
        yield Horizontal(self._approve, self._reject, classes="hr_buttons")
        yield self._why

    def on_mount(self) -> None:
        self.show(self.p)

    @property
    def decidable(self) -> bool:
        return self.p["state"] in ("proposed", "approved")

    def show(self, p: dict) -> None:
        self.p = p
        self._head.update(head(p))
        self._body.update(body(p))
        self.set_classes(p["state"])
        self.query_one(".hr_buttons").display = self.decidable
        if not self.decidable:
            self.remove_class("rejecting")

    def approve(self) -> None:
        if self.decidable:
            self._finish("approved", "")

    def reject(self) -> None:
        """Ask why; the reason, once entered, is the rejection."""
        if self.decidable:
            self.add_class("rejecting")
            self._why.focus()

    def _finish(self, verdict: str, note: str) -> None:
        ok, said = self._decide(self.p, verdict, note)
        if ok:
            self.show({**self.p, "state": "applied" if verdict == "approved" else verdict, "error": None})
        self.remove_class("rejecting")
        self.post_message(self.Decided(self, ok, said))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.approve() if event.button is self._approve else self.reject()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.value.strip():
            self._finish("rejected", event.value.strip())

    def on_key(self, event) -> None:
        if event.key == "escape" and self.has_class("rejecting"):
            event.stop()
            self.remove_class("rejecting")
