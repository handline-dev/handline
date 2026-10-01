"""A `Static` whose drag-selection is read from its wrapped lines."""
from __future__ import annotations

from textual.content import Content
from textual.selection import Selection
from textual.widgets import Static


class WrappedStatic(Static):
    """Textual's `get_selection` indexes the unwrapped text with on-screen line numbers, so a
    selection on the second visual line of a one-line string runs past its end."""

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        visual = self._render()
        if not isinstance(visual, Content):
            return super().get_selection(selection)
        width = self.content_size.width
        text = "\n".join(line.plain for line in visual.wrap(width)) if width > 0 else visual.plain
        return selection.extract(text), "\n"
