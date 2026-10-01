"""An app whose copy reaches the system clipboard."""
from __future__ import annotations

from textual import events
from textual.app import App

from ..infra import clipboard


class ClipboardApp(App):
    """An app whose copy also reaches the system clipboard: Textual's own copy
    is OSC 52, which iTerm2 and macOS Terminal drop unless told otherwise. A
    drag-selection is copied when the mouse is let go, as Cmd+C never reaches
    the app."""

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.call_after_refresh(self._copy_selection)

    def _copy_selection(self) -> None:
        try:
            text = self.screen.get_selected_text()
        except IndexError:  # a widget whose get_selection misreads its own lines: copy nothing, keep the app
            return
        if text:
            self.copy_to_clipboard(text)

    def copy_to_clipboard(self, text: str) -> None:
        super().copy_to_clipboard(text)
        if clipboard.copy(text):
            self.notify(f"copied {len(text)} chars", timeout=2)
