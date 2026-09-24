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
import importlib

from . import (delivery, devmode, evaluate, handoff, lifecycle, locks, loop, reconciler,  # noqa: F401
               states, usage_poll)

# Every name the daemon defines, and the module it lives in. A name is looked up
# here when it is asked for, not copied into this namespace, so a test that
# replaces one in its own module is seen by callers outside the package too.
_HOME = {
    "states": ("IDLE", "QUEUED", "RUNNING", "DONE", "HANDOFF", "DEGRADED", "PARKED", "THROTTLED",
             "STALLED", "EVALUATED", "WAKER", "MOVING_MARK", "BY_HAND_MARK", "park_wakeable",
             "LIMIT_SETTLE_S", "DEGRADED_RETRY_S", "ASSUMED_ACCOUNT_RECHECK_S",
             "LOGIN_CHECK_COOLDOWN_S", "USAGE_POLL_S", "USAGE_MAX_AGE_S",
             "USAGE_REFRESH_COOLDOWN_S", "USAGE_FAIL_BACKOFF_CAP_S", "MAX_FAILED_ATTEMPTS",
             "MAX_THROTTLE_STREAK", "IDLE_CLOSE_S", "PROMPT_CACHE_S", "RESUME_WARM_S",
             "STUCK_RUNNING_S", "HANDOFF_DEADLINE_S", "BACKOFF_BASE_S", "BACKOFF_MAX_S",
             "MIN_HANDOFF_INTERVAL_S", "backoff_for"),
    "locks": ("DAEMON_LOCK", "running_elsewhere", "_supervises_this_home", "processes"),
    "delivery": ("_seen", "_seen_at", "SEEN_FRESH_S", "session_present", "END_WAIT_S",
             "EXIT_DIALOG_WAIT_S", "END_POLL_S", "_wait_session_gone", "WAKE_SESSION_TIMEOUT_S",
             "wait_for_session", "_past_dialog", "_stalled_on_dialog", "_reclaim_context",
             "wall_reason", "_live_session", "_session_changed", "TYPE_SETTLE_S", "_type_gate",
             "_mark_typed", "deliver_queued", "NUDGE_AFTER_S", "FORGOTTEN_STUCK_S",
             "NUDGE_RETRY_S", "track_background_work", "idle_since", "has_background_work",
             "forgotten_resolve", "nudge_forgotten_resolve", "notify_cancelled", "_answer_verb",
             "_tell", "ASKED_SHOWN", "answer_line", "deliver_answers"),
    "lifecycle": ("should_close", "triggers_apply", "would_wake", "_cold_before_typing",
             "close_agent", "_close_agent", "_decline_exit_dialog", "_end_session",
             "cache_expired", "wake_prompt", "_start_for_work", "_write_wake_prompt", "wake_agent",
             "_wake_agent", "_same_path", "_clear_trust_prompt", "_handle_trust_prompt",
             "TRUST_STALL", "_escalate_trust_prompt"),
    "reconciler": ("expire_handoffs", "_stall_agent", "reconcile", "_probe_turn",
             "_resumable_session", "sweep", "silent_minutes", "_unstick_running"),
    "handoff": ("_record_handoff", "resume_handoff", "force_move", "perform_handoff",
             "MOVABLE_TRIGGERS", "USAGE_TRIGGERS", "_handoff_backing_off", "_last_left",
             "_move_plan", "_forced_plan", "_move_to_room", "_perform_handoff"),
    "evaluate": ("Tick", "_SAID", "_SAID_CLOSE", "_running_account", "_confirmed_reading",
             "_queued_or_idle", "supervise", "tick", "observe_burn"),
    "usage_poll": ("_last_rollover_day", "collect_snapshots", "usage_due", "poll_usage",
             "_usage_backoff", "_ended_window", "_poll_account"),
    "devmode": ("DEV_MODE_KEY", "dev_mode_enabled", "set_dev_mode", "_watched_files",
             "_code_fingerprint", "_dev_import_check", "_dev_mode_should_restart",
             "_dev_mode_restart"),
    "loop": ("run"),
}


def __getattr__(name):
    for module, names in _HOME.items():
        if name in names:
            return getattr(importlib.import_module(f"{__name__}.{module}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
