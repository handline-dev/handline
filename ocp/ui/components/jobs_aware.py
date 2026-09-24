"""What a screen needs to run a verb through `jobs.Jobs` and keep showing it."""
from __future__ import annotations

from ... import actions, tui, jobs as jobsmod
from .confirm import Confirm
from .text_prompt import TextPrompt


class JobsAware:
    """What a screen needs to run a verb through `jobs.Jobs` and keep showing
    it: one `Jobs` per app (`self.app.jobs`), shared across screens so an
    action started from the dashboard is still there, still running, if the
    human moves to another screen while it goes."""

    REFRESH_S = 2.0
    BUSY_REFRESH_S = 0.25  # while a job is running, quick enough to notice it end
    flash = ""

    @property
    def jobs(self) -> jobsmod.Jobs:
        return self.app.jobs

    def _retime(self) -> None:
        if getattr(self, "_timer", None) is not None:
            self._timer.stop()
        self._timer = self.set_interval(
            self.BUSY_REFRESH_S if self.jobs.running else self.REFRESH_S, self._tick)

    def _tick(self) -> None:
        said = self.jobs.reap()
        if said:
            self.flash = "  ·  ".join(said)
            self._retime()
        self._redraw()

    def _start(self, verb: str, target: str, what: str, args: tuple, kwargs: dict | None = None,
               on_result=None) -> str:
        said = self.jobs.start(verb, target, what, args, kwargs, on_result)
        self._retime()
        return said


async def dispatch_agent_verb(screen: JobsAware, con, name: str, label: str) -> None:
    """Run one `AGENT_KEYS` verb against `name`, whichever screen it was
    pressed or typed on: what asks first and what needs a prompt is decided
    here, the work goes through `jobs.Jobs`."""
    allowed, why = actions.can(con, name, label)
    if not allowed:
        screen.flash = why
        return
    if label in ("message", "editor"):
        # "editor" is one more way to write the same message, not a different
        # action (`actions.can` has no case for it either): the modal composer
        # stands in for both.
        body = await screen.app.push_screen_wait(TextPrompt(f"message {name}:", multi=True))
        if body is None:
            screen.flash = "nothing sent: try again to retry"
            return
        screen.flash = screen._start("message", name, f"messaging {name}", (name, body))
        return
    if label == "terminal" and tui._needs_a_terminal(con, name):
        repo = await screen.app.push_screen_wait(
            TextPrompt(f"{name} has nowhere to run - repo to start it in:"))
        if not repo:
            screen.flash = "not started"
            return
        screen.flash = screen._start("terminal", name, f"starting {name}", (name, repo.strip()))
        return
    if label == "move":
        dest = await screen.app.push_screen_wait(TextPrompt(f"move {name} to which account:"))
        if not dest:
            screen.flash = "not moved"
            return
        dest = dest.strip()
        if not await screen.app.push_screen_wait(
                Confirm(f"move {name} to {dest} - records its handoff, ends its session, "
                        f"and starts a new one there")):
            screen.flash = "left alone"
            return
        screen.flash = screen._start("move", name, f"moving {name} to {dest}", (name, dest))
        return
    if tui._needs_confirm(con, name, label):
        if not await screen.app.push_screen_wait(Confirm(f"{label} {name}")):
            screen.flash = "left alone"
            return
        ok, why = tui._confirmed_can(con, name, label)
        if not ok:
            screen.flash = why
            return
    screen.flash = screen._start(label, name, f"{jobsmod.GERUND.get(label, label)} {name}", (name,))
