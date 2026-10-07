"""One screen row with SGR escape codes (what `tmux capture-pane -e` writes) as its text and style runs, for the `styled` terminal format.

The text is the row without its trailing blanks, as the `text` format has it. A run is [from, to) over the row's code points; blank cells
that carry a background count one each past the end of the text, as the protocol says. Only SGR is read: every other escape sequence and
control character is dropped."""
from __future__ import annotations

import re

_ESC = re.compile(r"\x1b(?:\[([0-?]*)[ -/]*([@-~])|\][^\x07\x1b]*(?:\x07|\x1b\\)|[\x20-\x2f]*[\x30-\x7e])")
_FLAGS = {1: ("bold", True), 2: ("dim", True), 3: ("italic", True), 4: ("underline", True), 7: ("inverse", True), 9: ("strike", True),
          22: ("bold", False), 23: ("italic", False), 24: ("underline", False), 27: ("inverse", False), 29: ("strike", False)}


def _extended(args: list[int], i: int) -> tuple[int | str | None, int]:
    """The colour that follows a 38 or 48 at args[i], and the index after it."""
    if i + 1 < len(args) and args[i + 1] == 5 and i + 2 < len(args):
        return (args[i + 2] if 0 <= args[i + 2] <= 255 else None), i + 3
    if i + 1 < len(args) and args[i + 1] == 2 and i + 4 < len(args):
        r, g, b = args[i + 2:i + 5]
        return (f"#{r:02x}{g:02x}{b:02x}" if all(0 <= c <= 255 for c in (r, g, b)) else None), i + 5
    return None, len(args)


def _sgr(style: dict, params: str) -> dict:
    """`style` after the SGR parameters `params` (a copy: the caller's dict is not changed)."""
    style = dict(style)
    # 38:2::r:g:b has an empty colour-space id after the 2; it is not an argument of the colour
    params = re.sub(r"([34]8):2::", r"\1:2:", params)
    args = [int(p) if p else 0 for p in params.replace(":", ";").split(";")] or [0]
    i = 0
    while i < len(args):
        n = args[i]
        i += 1
        if n == 0:
            style = {}
        elif n in _FLAGS:
            key, on = _FLAGS[n]
            if on:
                style[key] = True
            else:
                style.pop(key, None)
                if n == 22:
                    style.pop("dim", None)
        elif 30 <= n <= 37 or 90 <= n <= 97:
            style["fg"] = n - 30 if n < 90 else n - 90 + 8
        elif 40 <= n <= 47 or 100 <= n <= 107:
            style["bg"] = n - 40 if n < 100 else n - 100 + 8
        elif n in (38, 48):
            color, i = _extended(args, i - 1)
            if color is not None:
                style["fg" if n == 38 else "bg"] = color
        elif n in (39, 49):
            style.pop("fg" if n == 39 else "bg", None)
    return style


def parse(raw: str) -> tuple[str, list[dict]]:
    """(text, spans) of one row of `raw`."""
    cells: list[tuple[str, dict]] = []
    style: dict = {}
    pos = 0
    for m in _ESC.finditer(raw):
        cells.extend((c, style) for c in raw[pos:m.start()] if c >= " " and c != "\x7f")
        pos = m.end()
        if m.group(2) == "m" and re.fullmatch(r"[0-9;:]*", m.group(1)):
            style = _sgr(style, m.group(1) or "0")
    cells.extend((c, style) for c in raw[pos:] if c >= " " and c != "\x7f")
    text = "".join(c for c, _ in cells).rstrip()
    while cells and cells[-1][0] == " " and not cells[-1][1]:
        cells.pop()
    spans: list[dict] = []
    for x, (_c, st) in enumerate(cells):
        if not st:
            continue
        if spans and spans[-1]["to"] == x and spans[-1]["style"] == st:
            spans[-1]["to"] = x + 1
        else:
            spans.append({"from": x, "to": x + 1, "style": st})
    return text, spans
