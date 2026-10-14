"""What a human can do to an agent, in one place.

`hl` is the screen the team is run from, so what it does has to live
somewhere that is not the drawing code: a screen that decided anything for
itself would be a second opinion, and this file exists so there is only ever
one.

Two halves. The acting half - unstall, message, answer, dismiss - is a line of
glue each over the call the daemon or the CLI makes, and returns the one line to
show for what happened. The reading half - messages and the details behind a
row - asks the database the questions a screen asks.
"""

from .routing import (HUMAN, _TEAM, routing, supervisor_of, listing_refusal, listable_agents, reach, agent_tree)  # noqa: F401
from .task_timeout import task_timeout, timeout_text, timeout_lines  # noqa: F401
from .dialogs import add_answer, dialog_of, standing_answers, remove_answer  # noqa: F401
from .prompt_blocks import open_blocks, open_block_of, headline as block_headline, request_answer as request_block_answer, answer as answer_block  # noqa: F401
from .control import unstall  # noqa: F401
from .requests import (message, message_ok, answer, answer_ok, record_human_resolve, dismiss, waiting_ids, cancel_plan, cancel, send_now, check_human_resolve, _is_answer_to_human, mailbox_rows, unseen_rows, for_you_seen, mark_read, fold_key, messages, founder_waits, opened_by_human, message_detail, QUOTE_LINES, _clip, EXCHANGE_ROWS, EXCHANGE_WIDTH, _exchange, _gist, _at, first_line)  # noqa: F401
from .task_tree import task_tree  # noqa: F401
from .tasks import memos_of, task_timeline, memo_add, memo_edit, memo_delete, tell_memo_holder, memo_summary  # noqa: F401
from .tasks import (TASK_REF, task_ref, task_refs_in, parse_task_ref_error, parse_task_ref, looks_like_sha, _sha_on_main, _UNBLOCK_REQUESTER, task_asker, task_state, child_blocker_of, child_wait, block_wait, _asks, decide, open_decision, task_mismatch, _request_answered, mark_task_done, discard_task, reopen_task, advance_current_task, release_ended_blockers, task_begin, task_handoff, assign_task, assign_targets, _begun, open_task_count, open_task_counts, reads_doing, release_task, MAX_TASK_TIMEOUT_S, DEFAULT_TASK_TIMEOUT_S, parse_task_timeout, _notify_unblocked, _clear_blocks_on, _task_edit_guard, PRIORITIES, priority_ref, parse_priority, EXPECTS, parse_expects, task_new, task_start, edit_task, task_brief, tell_body_edited, send_moved_task, set_queued, set_queued_all, _under, _merge_refusal, merge_tasks, task_rows, _blocked_by_cycle, _blocker_refusal, add_block, unblock, reason_gaps, STALE_AFTER_S, BATCH_VERBS, task_idle_s, task_stale, task_related_to, TASK_STATES, OPEN_STATES, DELEGATED_STATES, TASK_SORTS, sort_task_rows, batch_task, has_team, recent_assignees, recent_chatted, question_targets, question_body, ask_on_task)  # noqa: F401
from .schedule import schedule_rows, schedule_add, schedule_edit, schedule_fields, schedule_remove, next_run, schedule_cron, schedule_words, SCHEDULE_KINDS, WEEKDAYS  # noqa: F401
from .paths import find_paths, open_path, path_bases  # noqa: F401
from .say import (READONLY_BASH_ALLOWLIST, LOOKUP_BUDGET, is_lookup_bash)  # noqa: F401
from .conversation import Row, Event, chat_reply, chat_reply_ok  # noqa: F401
from .details import (idle_close_minutes, parse_idle_close, set_idle_close, agent_knows, agent_done, agent_where, agent_worktree, _config_line, account_facts, set_account_cap, save_account_settings, context_share, set_default_cap, set_cycle_on, set_cycle_end, GLOBAL_SWITCHES, global_switches, save_global_switches, weekly_outlook, agent_use, agent_overlay, daily_agent_cost, eligible_worktrees, trust_worktrees, trust_each, _trust_one_worktree)  # noqa: F401
from .shell import command_of, cwd_for, ShellRun  # noqa: F401
