# Mobile agent apps: what to learn for our phone app

Story 10-3, research only. 56 App Store screenshots (`IMG_8418.PNG` to `IMG_8473.PNG`, from `orca-references/apps`) of mobile apps that
run or watch coding agents, read one by one. Ideas only: no code, text or assets are taken from them. Two of the apps never show their name;
they are described by what they are. The names below are as the screenshots show them.

## The apps

| App | Files | For | First run | Main menu | Sessions / agents | Chat | Tasks | Needs you / approvals | Usage |
|---|---|---|---|---|---|---|---|---|---|
| Moshi (Mosh terminal) | 8418–8424 | A terminal that keeps Claude Code and Codex sessions alive through sleep, app exit and network change | not shown | Inbox / Usages switch at the top | one terminal per session, tab bar | typed or dictated into the terminal; paste, crop, annotate images | none | Inbox feed: "Approve write to /etc/hosts" with a countdown ring, also on lock screen and Dynamic Island | 5 h and 7 d bars per account with a pace tick and "pace slower" |
| T3 Code | 8425–8430 | One inbox for agents on several machines | not shown | "…" menu; Environments sheet | one list, each row: repo, title, branch, machine, state (Working, Approval, Unsent, Snoozed, Settled) | chat with Terminal, Files, Diff buttons in the header | none | state word on the row; lock-screen card "3 active agents · 2 need attention" | none |
| Happy | 8431–8435 | Agent chat on the phone with a desktop companion | not shown | Sessions list with filter and settings | list with a face, project, branch, time, "Show archived" | team chat with several people and agents; model picker | none | none shown | none |
| Hermex | 8436–8443 | A phone client for a Hermes agent server | not shown | Bots, Tasks, Kanban, Skills, Memory, Usage, Active Profile, Projects, Scheduled sessions, then Sessions with a Chat button | bots as faces; sessions with "Working", message count, project | chat with Thinking and tool-call rows, stop button, slash commands | scheduled jobs with Run now, Pause, run history | "Approval required": Allow once / Allow session / Always allow / Deny / Skip all; time-sensitive notification and a Live Activity | Usage menu row (screen not shown) |
| Railway | 8444–8448 | Projects and deploys, with an AI chat | not shown | bottom bar: overview, services, metrics, logs, chat | projects as favourites and a list | chat that stages a change, then Apply | none | notification "Build failed" with a diagnosis and one-tap fix | CPU, memory, egress charts over 1 h to 30 d |
| Agent-chat app (name not shown) | 8449–8451 | Agent chat with voice and a review sheet | not shown | menu button | thread list | chat with Thinking and Shell rows | none | none shown | none |
| Codex client (name not shown) | 8452–8456 | Codex chat with quick actions, files and terminal | not shown | menu button | recent chats | chat; after a review three pills: Write tests, Refactor, Explain | none | none | none |
| General AI assistant (Claude and GPT-4o API) | 8457–8461 | A chat assistant, not an agent tool | not shown | Explore prompts, Chat, Detect, Recent | recent chats | plain chat | none | none | none |
| Cursor (mobile) | 8462–8467 | Start and follow Cursor agents from the phone | not shown | All Workspaces: Pinned, Today, by repo; search and filter | rows with repo, Working / Passed / No Changes / Merged, +lines −lines | follow-up box with voice, model chip, branch chip | each agent is a task: demos, review, squash and merge | lock-screen "Finished … Review" and "5 Active" list | none |
| BotBus | 8468–8473 | Many agents on many computers, plus Apple Watch | not shown | Computers list; a computer; New Task | computers with Online, tasks, running, pending; projects with the agent kind | chat with a "Wants to run a command" card | New Task: pick project and connector | Approve / Deny on phone and on the watch; "2 pending" on the computer card | none |

## What our phone app has today

Five tabs (Agents, Inbox, Tasks, Accounts, Stats) in `apps/mobile/src/screens/Home.tsx`. Agents list puts agents that need the person first
and shows the needs-you card at the bottom of the chat. Chat shows delivery states (queued, sent, confirmed, unknown), search, reply, and
images. Structured agents show a transcript with permission cards. Agent strip shows model, account, context and cache time. Stats has the usage
outlook. Schedules, worktrees, team, HR proposals, memos and task verbs exist. One daemon is paired at a time. There is no push or lock-screen
surface, no voice, no diff view, no terminal key bar, and no machine list.

## Improvements, most valuable first

1. **Needs you outside the app.** A notification and an iOS Live Activity that name the agent, the kind and the one line, and open the
   card. Seen in `IMG_8422.PNG`, `IMG_8430.PNG`, `IMG_8439.PNG`, `IMG_8467.PNG`. This is the main gap: today the person has to open the app to learn
   that an agent waits. Needs a push path from the daemon.
2. **One header count on the feed.** "3 active · 2 need attention", and each row carrying a state word (Working, Approval, Input, Done) rather
   than colour alone. `IMG_8425.PNG`, `IMG_8430.PNG`, `IMG_8467.PNG`.
3. **Approve with a scope.** One tap to Approve or Deny on the card and, where the daemon allows it, "this session" or "always" next to
   "once". `IMG_8438.PNG`, `IMG_8473.PNG`. Only offer a scope the daemon serves.
4. **A diff you can read before merging.** Files with +/− counts, collapsible per file, and a Ready to merge bar with the checks.
   `IMG_8429.PNG`, `IMG_8435.PNG`, `IMG_8443.PNG`, `IMG_8451.PNG`, `IMG_8467.PNG`. We have Merge on a task page but nothing to read first.
5. **Voice input on chat.** Hold or tap to dictate, a timer while recording, edit before send. `IMG_8419.PNG`, `IMG_8450.PNG`, `IMG_8463.PNG`.
6. **Mark up a picture before sending.** Numbered pins and a short note per pin, crop and draw. `IMG_8420.PNG`, `IMG_8464.PNG`. Our picker sends the
   picture as it is.
7. **Machines list.** Each machine with Online, number of agents, running, pending, last seen, and which are offline. `IMG_8426.PNG`,
   `IMG_8472.PNG`, `IMG_8468.PNG`. Ties to the multi-machine research (docs/story4-multi-machine-research.md).
8. **Pace on usage.** A tick on each bar for where an even spend would be, the words "slower" or "faster", and the time to reset. `IMG_8421.PNG`.
   Ours shows the week and runway; the even-spend tick is new.
9. **Show the work.** Screenshots or a short video attached to a finished turn, and a live preview of the dev server the agent started.
   `IMG_8465.PNG`, `IMG_8466.PNG`, `IMG_8428.PNG`, `IMG_8470.PNG`. Needs the daemon to carry artifacts.
10. **Schedules with Run now, Pause and run history.** `IMG_8442.PNG`. Ours lists, adds, edits and removes.
11. **Next-step pills after a turn.** "Review +2 −2", "Squash & Merge", or Write tests / Refactor / Explain under the last message.
    `IMG_8465.PNG`, `IMG_8454.PNG`. Cheap and uses verbs we already have.
12. **Terminal key bar and saved shortcuts** (Esc, Tab, Ctrl, arrows, a custom chord) if the phone is to type into a terminal.
    `IMG_8418.PNG`, `IMG_8423.PNG`, `IMG_8428.PNG`, `IMG_8456.PNG`.
13. **Say that a session survived.** "Reconnecting to the existing session" and a cue that nothing was lost after sleep or a network change.
    `IMG_8418.PNG`, `IMG_8456.PNG`. Our delivery states cover messages; the page itself says nothing.
14. **Model chooser with search, grouped by provider, with favourites.** `IMG_8431.PNG`, `IMG_8441.PNG`. Ours is a field in agent settings.
15. **Session rows with a face, last activity time and an archived group.** `IMG_8432.PNG`, `IMG_8437.PNG`, `IMG_8440.PNG`.
16. **One-tap fix inside a failure notice.** A diagnosis and a single action, or "Continue in chat". `IMG_8447.PNG`.
17. **Stage, then apply.** Show a staged change with Apply instead of acting at once. `IMG_8448.PNG`. Fits our "decided" writes.
18. **Apple Watch approvals.** `IMG_8470.PNG`, `IMG_8468.PNG`. Later, after item 1.
19. **Themes.** `IMG_8424.PNG`. Lowest value.

## Not for us

`IMG_8457.PNG` to `IMG_8461.PNG` (prompt library, AI-text detector, recents of a general assistant) have nothing for agent work.
