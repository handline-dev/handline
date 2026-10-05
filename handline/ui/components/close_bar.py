"""The one visible way to close a box over the page: a small `✕ close` in its top-right corner, on the
border line, for every modal. A click on it asks the screen to `close()`: the same answer Escape gives."""
from __future__ import annotations

LABEL = " ✕ close "
CORNER_W = len(LABEL) + 3     # the label, the cell of padding Textual puts each side of a border title, and the corner


def in_close_corner(left: int, top: int, width: int, x: int, y: int) -> bool:
    """Whether a click at screen cell (x, y) is on the top border of a box at (left, top), `width` wide,
    within its last CORNER_W cells."""
    return y == top and left + width - CORNER_W <= x < left + width


class CloseCorner:
    """A mixin for a ModalScreen drawn in `#dialog`: marks the corner and closes on a click there.
    `CLOSE_CORNER = False` leaves it off where Escape already has a button of its own."""

    CLOSE_CORNER = True

    def mark_close_corner(self) -> None:
        if not self.CLOSE_CORNER:
            return
        box = self.query_one("#dialog")
        box.border_title = LABEL
        box.styles.border_title_align = "right"
        box.styles.border_title_color = "white"
        box.styles.border_title_style = "bold"

    def on_click(self, event) -> None:
        if not self.CLOSE_CORNER:
            return
        r = self.query_one("#dialog").region
        if in_close_corner(r.x, r.y, r.width, event.screen_x, event.screen_y):
            event.stop()
            self.close()
