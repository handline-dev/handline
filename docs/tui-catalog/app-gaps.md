# TUI vs app: what the app lacks

For each entry of [index.md](index.md): does the app (`apps/web`, in Electron `apps/electron`) have it? **yes** / **partly** / **no**, with the file that does it. Found by reading `apps/web/src`, not by running the app, so "yes" means the code is there, not that I used it. `src` below is `apps/web/src`.

## Gaps by importance

### No (nothing in the app)

1. **Full output of a shell command** (modal-output, "Show all" on a shell card). The app's card shows the command only (`ChatMessages.tsx:277`); the printed output and the "early lines let go" count have no view.
2. **Whole handoff of a session** (modal-handoff). The app shows one line, `Handoff: <text>` (`ChatMessages.tsx:290`), not the scrollable whole.
3. **Add an account** (accounts-add). The app tells the person to run `hl account add <name>` on the host (`Accounts.tsx:59-67`).
4. **Reject all** in HR (R). The app has Approve all, not Reject all (`HrTab.tsx:44-75`).

### Partly

5. **Daemon down** (home-daemon-down): a status-bar icon and tooltip (`StatusBar.tsx:18`), no page that says the daemon is stopped and what to do.
6. **Key lists per panel** (the `?` entries): the app has one global list of 5 keys (`KeysSheet.tsx`) plus the Inbox and Tasks lists; Stats and the account/chat pages have none.
7. **Account Login** (account-settings Login button): starts the vendor flow in a daemon terminal (`Accounts.tsx:183`), from the list, not from the account's settings.

### Yes

Everything else in the catalog. Table below.

## Entry by entry

| Catalog entry | App | Where |
|---|---|---|
| home-agents, home-agents-keys | yes | `Sidebar.tsx` (cache left, context), `TeamPage.tsx`, `KeysSheet.tsx` |
| home-accounts, home-accounts-keys | yes | `Accounts.tsx` (the `?` list: gap 6) |
| home-messages, home-messages-keys | yes | `Inbox.tsx` (own `?`) |
| tab-stats, stats-view, stats-range, stats-screen | yes | `Stats.tsx`, `Outlook.tsx` (view and range selectors) |
| stats-keys | partly | gap 6 |
| tab-hr, hr-proposals, hr-reject, hr-withdraw | yes | `HrTab.tsx` |
| hr-edit-settings | yes | `HrTab.tsx:231` goes to `SettingsView.tsx` |
| tab-settings | yes | `Settings.tsx`, `SettingsView.tsx` |
| tasks-list, tasks-tree, tasks-closed, tasks-states, tasks-agent-filter | yes | `Tasks.tsx`, `TasksHead.tsx` |
| tasks-sorted, tasks-sort-reverse | yes | `Tasks.tsx:129` |
| tasks-marked, tasks-all-marked | yes | `Tasks.tsx:145,159` (A), `TaskBar.tsx` |
| tasks-owner, tasks-priority, tasks-discard, tasks-merge | yes | `TaskBar.tsx`, `TaskSheets.tsx` |
| tasks-keys | yes | `TaskSheets.tsx` key sheet |
| task-detail | yes | `TaskDetail.tsx` |
| task-assign, task-done, task-discard | yes | `TaskDetail.tsx` (owner edit, `ReasonSheet`) |
| not-captured: u (queue/draft), r (reopen), memo, brief edit | yes | `TaskDetail.tsx:48-60` |
| agents-message, agents-close, agents-restart, agents-park, agents-fold, agents-move, agents-wake | yes | `AgentVerbs.tsx`, `ChatMenus.tsx` |
| accounts-cap, account-settings (alias, cap, recovery, month end), accounts-remove | yes | `AccountSettings.tsx`, `AccountRemove.tsx` |
| accounts-add | no | host command only |
| messages-answer, messages-dismiss, messages-mark-read, messages-mark-all | yes | `Inbox.tsx:14-17`, `InboxCard.tsx` |
| account-overview | yes | `Accounts.tsx` |
| account-agents | yes | `Accounts.tsx:13` Agents tab |
| chat-backend, chat-all | yes | `ChatTab.tsx:159` |
| chat-tasks, chat-settings | yes | `Workspace.tsx`, `SettingsView.tsx` |
| chat-history (ctrl+r), chat-slash | yes | `ChatCompose.tsx:1,110`, `ChatMenus.tsx` |
| modal-question, modal-question-multi, modal-permission, modal-dialog | yes | `PromptCard.tsx` (inline card, not a modal; "always" kept as a rule) |
| modal-handoff | no | gap 2 |
| modal-output | no | gap 1 |
| modal-confirm-restart | yes | `AgentVerbs.tsx:64` |
| modal-confirm-skip-permissions, modal-confirm-no-sandbox | yes | `SettingsView.tsx:104,187` (`DANGER` confirm) |
| modal-schedule-add, modal-schedule-edit | yes | `Schedules.tsx` |
| modal-site-add, modal-site-edit | partly | `SettingsView.tsx:213` adds and removes a site; no edit in place |
| modal-answer-add | yes | `Answers.tsx` |
| home-daemon-down | partly | gap 5 |
| empty-home, empty-tasks, empty-stats, empty-hr | yes | `Empty.tsx` in each page (`Accounts.tsx:100` for accounts) |
| t (terminal), e (editor) | yes | `TerminalTab.tsx`, `EditorButton.tsx`; Electron `editor.ts` |

## Not checked

Nothing was run: this list comes from the code. Shot-by-shot comparison with a running app is for the person who decides. No TUI code was touched.
