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

### chat-slash

Typing / in the compose box lists the agent's slash commands.

![chat-slash](img/chat-slash.svg)


## States

### home-daemon-down

The home page with the daemon stopped.

![home-daemon-down](img/home-daemon-down.svg)


## Not captured

- Shots are SVG (no PNG converter on the build machine); open them in a browser.
- Modals are shown up to the question; no answer is sent, so no result screen (confirm done, error toast) is shown.
- Stats, HR and Settings tabs show the scratch home's thin data: no weekly readings, no per-agent usage rows.
- Opened outside the TUI, so not shot: t (terminal), e (editor), the account Login flow.
- Empty states other than daemon-down (no agents, no tasks, no messages) are not shot; error and long-text states are not shot either.
- Modals: Question, Permission, Dialog (answering an agent's blocked prompt in chat), Handoff, ScheduleAdd, SiteEdit, AnswerAdd, Output (shell card), and the f5 restart and agent-settings Confirms (skip permissions, no sandbox).
- Task detail verbs a/x/u/r/d (assign, done, reopen, discard), memo and brief edit; Tasks tab A and S.
- HR proposals (a/A/r/R/w/e) and the agent settings screen: no proposals are seeded.
- Stats: r/v/backspace/? on WEEK LEFT and the full Stats screen (S).
- Chat ctrl+r history; Home w (wake) and M (mark all read).
