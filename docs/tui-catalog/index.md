# TUI catalog

One screenshot per screen, tab, modal and state of the Textual IDE.


## Home

### home-agents

The home page opens on AGENTS: one row per agent with account, model, state, cache, context, inbox, task.

![home-agents](img/home-agents.svg)

### home-agents-keys

The key list of the AGENTS panel (?).

![home-agents-keys](img/home-agents-keys.svg)

### home-accounts

ACCOUNTS panel: each account's 5h and 7d usage, resets, agents on it, runway.

![home-accounts](img/home-accounts.svg)

### home-accounts-keys

The key list of the ACCOUNTS panel (?).

![home-accounts-keys](img/home-accounts-keys.svg)

### home-messages

FOR YOU panel: what agents have sent the founder, oldest unanswered first.

![home-messages](img/home-messages.svg)

### home-messages-keys

The key list of the FOR YOU panel (?).

![home-messages-keys](img/home-messages-keys.svg)


## Home tabs

### tab-stats

The stats tab of the home page.

![tab-stats](img/tab-stats.svg)

### tab-hr

The hr tab of the home page.

![tab-hr](img/tab-hr.svg)

### tab-settings

The settings tab of the home page.

![tab-settings](img/tab-settings.svg)


## HR tab

### hr-proposals

Roster proposals waiting on the founder: the list, the one under the cursor in full, the agents below.

![hr-proposals](img/hr-proposals.svg)

### hr-reject

r: reject the proposal, with a reason.

![hr-reject](img/hr-reject.svg)

### hr-edit-settings

e: the agent settings screen for the agent under the cursor in the agents list.

![hr-edit-settings](img/hr-edit-settings.svg)

### hr-withdraw

w: withdraw a proposal the founder raised (what it says on one an agent raised).

![hr-withdraw](img/hr-withdraw.svg)


## Stats

### stats-view

v: choose what the WEEK LEFT numbers are (session left, week left, ...).

![stats-view](img/stats-view.svg)

### stats-range

r: step the range (12h, 24h, 3d, 7d, 14d; 7d, 14d, 30d in the cost view).

![stats-range](img/stats-range.svg)

### stats-keys

?: the Stats key list.

![stats-keys](img/stats-keys.svg)

### stats-screen

S from Home: the Stats page on its own screen.

![stats-screen](img/stats-screen.svg)


## Tasks tab

### tasks-list

Every open task as a list: priority, state, title, owner, idle time, timeout, asker, blocked-by; the side pane shows the task under the cursor.

![tasks-list](img/tasks-list.svg)

### tasks-tree

The same tasks as a tree of what waits on what (t switches).

![tasks-tree](img/tasks-tree.svg)

### tasks-closed

Done and discarded tasks shown too (a toggles).

![tasks-closed](img/tasks-closed.svg)

### tasks-states

Pick which states to show, with checkboxes (v).

![tasks-states](img/tasks-states.svg)

### tasks-agent-filter

One agent's tasks; each f press moves to the next agent.

![tasks-agent-filter](img/tasks-agent-filter.svg)

### tasks-sorted

Sorted by the next column (s; S reverses; a click on a column head sorts by it).

![tasks-sorted](img/tasks-sorted.svg)

### tasks-marked

Rows marked with space; d, o, p, m act on all marked rows, and the bar below says so.

![tasks-marked](img/tasks-marked.svg)

### tasks-all-marked

A: mark every row shown.

![tasks-all-marked](img/tasks-all-marked.svg)

### tasks-sort-reverse

S: the sort reversed.

![tasks-sort-reverse](img/tasks-sort-reverse.svg)

### tasks-owner

Change the owner of the marked tasks (o).

![tasks-owner](img/tasks-owner.svg)

### tasks-priority

Set the priority of the marked tasks (p).

![tasks-priority](img/tasks-priority.svg)

### tasks-discard

Discard the marked tasks, with a reason (d).

![tasks-discard](img/tasks-discard.svg)

### tasks-merge

Fold the marked tasks into the row under the cursor (m).

![tasks-merge](img/tasks-merge.svg)

### task-detail

One task opened (enter): its state, memos, request and what it waits on.

![task-detail](img/task-detail.svg)


## Task page verbs

### task-assign

a on a task page: hand the task to another agent.

![task-assign](img/task-assign.svg)

### task-done

x on a task page: mark it done, with a reason.

![task-done](img/task-done.svg)

### task-discard

d on a task page: discard it, with a reason.

![task-discard](img/task-discard.svg)


## Tasks tab

### tasks-keys

The Tasks tab key list (?).

![tasks-keys](img/tasks-keys.svg)


## Home: agent verbs

### agents-message

m: type a message to the agent under the cursor.

![agents-message](img/agents-message.svg)

### agents-close

c: close the agent's session (asks first).

![agents-close](img/agents-close.svg)

### agents-restart

r: restart the agent's session (asks first).

![agents-restart](img/agents-restart.svg)

### agents-park

p: park the agent (asks first).

![agents-park](img/agents-park.svg)

### agents-fold

f: fold the agent's events into its digest.

![agents-fold](img/agents-fold.svg)

### agents-move

v: move the agent to another account (asks which).

![agents-move](img/agents-move.svg)


## Home: account verbs

### accounts-add

a: add an account by name.

![accounts-add](img/accounts-add.svg)

### accounts-cap

c: cap how many agents may run on the account.

![accounts-cap](img/accounts-cap.svg)

### accounts-remove

x: remove the account (asks first).

![accounts-remove](img/accounts-remove.svg)


## Home: message verbs

### messages-answer

a: answer the message under the cursor.

![messages-answer](img/messages-answer.svg)

### messages-dismiss

x: dismiss the message.

![messages-dismiss](img/messages-dismiss.svg)

### messages-mark-read

m: mark a message read away from FOR YOU (what waits on an answer stays).

![messages-mark-read](img/messages-mark-read.svg)


## Home: agent verbs

### agents-wake

w: wake the agent under the cursor (what it says when it is already working).

![agents-wake](img/agents-wake.svg)


## Home: message verbs

### messages-mark-all

M: mark every message read (what waits on an answer stays).

![messages-mark-all](img/messages-mark-all.svg)


## Account page

### account-overview

One account: usage windows, month cycle, how it counts on the home row, status.

![account-overview](img/account-overview.svg)

### account-agents

The agents running on the account, with their state.

![account-agents](img/account-agents.svg)

### account-settings

The account's settings: alias, cap, usage recovery, month end; Login and Remove buttons.

![account-settings](img/account-settings.svg)


## Chat page

### chat-backend

An agent's chat: the conversation, its work line, the compose box.

![chat-backend](img/chat-backend.svg)

### chat-all

All chat: everything said to and by the agent, including its peers' messages.

![chat-all](img/chat-all.svg)

### chat-tasks

The agent's tasks, in the same board as the Tasks tab.

![chat-tasks](img/chat-tasks.svg)

### chat-settings

The agent's settings: model, effort, account, switches.

![chat-settings](img/chat-settings.svg)

### chat-history

ctrl+r: what was sent before; typing narrows the list (empty here: nothing was sent from this scratch home).

![chat-history](img/chat-history.svg)

### chat-slash

Typing / in the compose box lists the agent's slash commands.

![chat-slash](img/chat-slash.svg)


## Modals

### modal-question

An agent's blocked question (one choice) answered from the screen.

![modal-question](img/modal-question.svg)

### modal-question-multi

The same for a question that takes several answers: checkboxes and Submit picked.

![modal-question-multi](img/modal-question-multi.svg)

### modal-permission

An agent waits for permission to run a tool; y or n answers it.

![modal-permission](img/modal-permission.svg)

### modal-dialog

An agent's blocked menu prompt: pick its number, or tick always to keep the pick as a rule.

![modal-dialog](img/modal-dialog.svg)

### modal-handoff

A session's handoff, whole and scrollable (enter on the session marker in chat).

![modal-handoff](img/modal-handoff.svg)

### modal-output

Everything a shell command printed (Show all on a shell card), with how many early lines were let go.

![modal-output](img/modal-output.svg)

### modal-confirm-restart

A confirm before a restart (f5 in chat).

![modal-confirm-restart](img/modal-confirm-restart.svg)

### modal-confirm-skip-permissions

Agent settings: turning off permission prompts asks first (danger style).

![modal-confirm-skip-permissions](img/modal-confirm-skip-permissions.svg)

### modal-confirm-no-sandbox

Agent settings: turning the sandbox off asks first (danger style).

![modal-confirm-no-sandbox](img/modal-confirm-no-sandbox.svg)

### modal-schedule-add

Add a schedule: when, and the message sent to the agent.

![modal-schedule-add](img/modal-schedule-add.svg)

### modal-schedule-edit

Edit a schedule: the form opens on its time and message.

![modal-schedule-edit](img/modal-schedule-edit.svg)

### modal-site-add

Add an allowed site (Settings tab, sites).

![modal-site-add](img/modal-site-add.svg)

### modal-site-edit

Edit a site.

![modal-site-edit](img/modal-site-edit.svg)

### modal-answer-add

Add an auto-answer rule (Settings tab): trust a folder, or answer a prompt for good.

![modal-answer-add](img/modal-answer-add.svg)


## States

### home-daemon-down

The home page with the daemon stopped.

![home-daemon-down](img/home-daemon-down.svg)


## Empty states

### empty-home

Home with one idle agent, no task and nothing waiting on the founder.

![empty-home](img/empty-home.svg)

### empty-tasks

The Tasks tab with no task.

![empty-tasks](img/empty-tasks.svg)

### empty-stats

The stats tab with nothing recorded.

![empty-stats](img/empty-stats.svg)

### empty-hr

The hr tab with nothing recorded.

![empty-hr](img/empty-hr.svg)


## Not captured

- Shots are SVG (no PNG converter on the build machine); open them in a browser.
- The modals in `Modals` are built with sample data, as the code builds them from a live session's prompt; they were not reached by a real blocked session.
- Modals are shown up to the question; no answer is sent, so no result screen (confirm done, error toast) is shown.
- Toasts (a refused verb, a saved setting) do not show in a shot.
- Stats and the Settings tab show the scratch home's thin data: no weekly readings, no per-agent usage rows.
- Opened outside the TUI, so not shot: t (terminal), e (editor), the account Login flow.
- Task page u (queue/draft) and r (reopen a done task); memo and brief edit.
- The f5 restart outside a modal, and approve (a, A) and reject-all (R) in HR: they change the roster.
- Error states: only the FATAL agent row is shot.
