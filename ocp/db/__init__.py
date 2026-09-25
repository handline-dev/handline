"""SQLite state: events, requests, cursors, agent state, handoff progress.

Cursors are row ids, not timestamps; see fold.py for why that matters.

Usage events carry a dedup key (assumption E): one API response becomes several
JSONL lines repeating the same usage object, which inflated burn rate by 2.4x
in the design's sample.
"""

from .schema import (SCHEMA, connect, OLD_HUMAN)  # noqa: F401
from .requests import (unannounced_answers, mark_announced, add_request, NEVER_REACHED_TARGET, chain_members, queued_for, head_of_queue, queue_depth, open_asks_of, open_escalations_of, mark_delivered, mark_send_now, send_now_pending, followups_pending, timeboxed_open, mark_timebox_noticed, mark_told_out_of_turn, retire_queued, LEGACY_DISMISS, dismissal, shown_verdict, close_if_queued, retire_if_undelivered, set_moot_by, get_request, is_still_queued, oldest_counted_ask, standing_denial, asks_since)  # noqa: F401
from .events import (add_event, FOLD_META_KINDS, events_after, latest_events, count_events, max_event_id)  # noqa: F401
from .cursors import (get_meta, set_meta, get_cursor, cursor_updated, clear_cursor, get_notified_session, set_notified_session, forget_told, set_cursor)  # noqa: F401
from .agent_state import (set_task, set_agent_state, set_state_unless_running, set_terminal, set_account, set_launch_fingerprint, set_closed, clear_closed, last_event_ts, AGENT_ACTIVITY_KINDS, last_activity, last_activity_id, last_account_activity, get_terminal, agent_of_terminal, bump_counter, reset_counter, due_agents, get_agent_state, all_agent_states)  # noqa: F401
from .tasks import (TASK_FIELDS, DEFAULT_PRIORITY, merge_task, move_task_links, task_last_activity, new_task, get_task, open_tasks, open_tasks_for, all_tasks, open_child_tasks, mark_task_done, close_task, discard_task, reopen_task, tasks_needing_close, edit_task, link_request, tasks_for_request, add_block, remove_block, task_blockers, blocks_on_ids, tasks_blocked_by, task_requests)  # noqa: F401
from .blocks import (block_get, blocks, block_open, block_update, block_set_request, press_add, presses_since, block_clear)  # noqa: F401
from .handoff import (start_handoff, advance_handoff, set_handoff, get_handoff, in_flight_handoffs, in_flight_for, last_handoff)  # noqa: F401
from .usage import (USAGE_SAMPLE_RETENTION_DAYS, record_usage_sample, prune_usage_samples, usage_samples)  # noqa: F401
