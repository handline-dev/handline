"""What a human can do to an agent, in one place.

`ocp bridge` is the screen the team is run from, so what it does has to live
somewhere that is not the drawing code: a screen that decided anything for
itself would be a second opinion, and this file exists so there is only ever
one.

Two halves. The acting half - wake, close, unstall, park, fold, start, message,
answer, dismiss - is a line of glue each over the call the daemon or the CLI
makes, and returns the one line to show for what happened. The reading half -
messages, stuck, and the details behind a row - asks the database
the questions a screen asks, and asks the daemon's own predicates for anything
it would otherwise have to judge for itself.

Two of them cannot be done from here at all. Logging in needs a human to type a
code into claude's own screen, and starting an agent needs a terminal to start
it in - so those return the command to run and let the caller hand the terminal
over.
"""

from .routing import (HUMAN, _TEAM, routing, supervisor_of, listing_refusal, listable_agents, reach, agent_tree)  # noqa: F401
from .dialogs import dialog_of, answer_dialog, standing_answers, remove_answer  # noqa: F401
from .confirmations import pending as pending_confirmation, pending_all as pending_confirmations, answer as answer_confirmation  # noqa: F401
from .control import (wake, close, unstall, park, fold, STARTED_TIMEOUT_S, start, terminal, login_argv, move, can, ASYNC_VERBS, _IDLE_WAIT_S, _DELIVERY_S, WAITS, perform)  # noqa: F401
from .requests import (message, message_ok, answer, answer_ok, check_open_proposal, record_human_resolve, dismiss, waiting_ids, cancel_plan, cancel, send_now, check_human_resolve, _is_answer_to_human, mailbox_rows, fold_key, messages, founder_waits, opened_by_human, message_detail, QUOTE_LINES, _clip, EXCHANGE_ROWS, EXCHANGE_WIDTH, _exchange, _gist, _at, first_line)  # noqa: F401
from .tasks import (finding_owner, TASK_REF, task_ref, parse_task_ref_error, parse_task_ref, open_finding_tasks, hold_refusal, open_hold_task, looks_like_sha, _sha_on_main, _UNBLOCK_REQUESTER, task_asker, task_state, _asks, task_ids_named, task_mismatch, _request_answered, _task_done_targets, mark_task_done, close_task, discard_task, reopen_task, _notify_unblocked, _clear_blocks_on, _task_edit_guard, PRIORITIES, priority_ref, parse_priority, edit_task, set_queued, _under, _merge_refusal, merge_tasks, task_rows, _blocked_by_cycle, _blocker_refusal, add_block, unblock, reason_gaps, STALE_AFTER_S, BATCH_VERBS, task_idle_s, task_stale, task_related_to, TASK_STATES, OPEN_STATES, TASK_SORTS, sort_task_rows, batch_task)  # noqa: F401
from .stuck import (STUCK_AFTER_S, _delivery_state, _WORLD, WORLD_FRESH_S, _delivery_block, queue_reason, _world, stuck)  # noqa: F401
from .conversation import (CHAT_CHAINS, CHAT_TAB, OTHERS_TAB, UNTOLD_TAB, _chat_rows, Row, Fact, Entry, Event, Said, conversation_and_drawn, message_place, chat_parent, chat_target, chat_reply, chat_reply_ok, _seen_key, chat_unread, chat_seen)  # noqa: F401
from .details import (agent_detail, agent_knows, agent_done, agent_where, agent_worktree, _config_line, account_facts, set_account_cap, save_account_settings, context_share, set_default_cap, weekly_outlook, agent_use, agent_overlay, eligible_worktrees, trust_worktrees, _trust_one_worktree)  # noqa: F401
