"""The daemon (design section 8). The part that makes this worth running.

Four rules from the design shape this loop.

Triggers are conditions, not edges (8.1). Every tick re-derives the world from
persisted state, so a crossing that happened while the daemon was dead is still
acted on when it comes back.

Everything gets evaluated, not just what is running (8.4). If only dispatched
agents were considered, then once everything is parked there is nothing left to
evaluate, and the event that would have resumed them can never fire. The system
would sit still with empty accounts forever.

Reconciliation runs unconditionally at startup (8.4). One pass covers triggers
missed while down and lost timers at the same time. In-memory timers are an
optimization and never the source of truth.

Handoff progress is committed per step (8.9), so a restart resumes from the
step it reached. An agent with an in-flight handoff is excluded from trigger
evaluation, and a second trigger merges into the existing one as an added
reason rather than starting a competing handoff.
"""
from . import (delivery, devmode, evaluate, handoff, lifecycle, locks, loop, reconciler,  # noqa: F401
               states, usage_poll)
