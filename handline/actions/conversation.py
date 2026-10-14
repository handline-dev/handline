"""The conversation with one agent: rows and replies."""
from __future__ import annotations

from ..infra import timefmt

from .requests import message_ok


class Row(str):
    """A row of an agent's page that the screen lays out from its fields.

    As a string it is the plain reading, which is all a caller that only
    searches needs. The fields are what the bridge draws from at the width the
    terminal has now, so a row is wrapped where it is drawn and its text never
    passes through anything that styles it.
    """


class Event(Row):
    """One thing the daemon or the agent did, with its whole payload."""
    ts: int
    kind: str
    payload: str

    def __new__(cls, ts: int, kind: str, payload: str):
        self = super().__new__(
            cls, f"{timefmt.stamp(ts, '%H:%M:%S')}  {kind:<18}{payload}")
        self.ts, self.kind, self.payload = ts or 0, kind, payload
        return self


def chat_reply(con, agent: str, body: str) -> str:
    """Send the human's message typed in the tab, outside any card: a plain
    message. Nothing is guessed to be an answer - a reply that is a decision is
    typed inside its card (`answer_ok` with that card's request)."""
    return chat_reply_ok(con, agent, body)[1]


def chat_reply_ok(con, agent: str, body: str) -> tuple[bool, str]:
    """`chat_reply`, and whether it went."""
    if not body.strip():
        return False, "nothing sent: no message typed"
    return message_ok(con, agent, body)
