# First run in other agent tools (story 10-1)

How other agent tools take a first-time user from nothing to a first finished piece of work, read from their code, step by step. Research only; nothing here is built. A last section reads the 56 App Store screenshots of mobile agent apps against `docs/mobile-apps-reference.md`. The steps follow 10-0: install and first launch, connect an account, add a project, folder trust, build a team or pick agents, give the first work, watch it, review, merge.

## The tools read

| Tool | Read at | License | State |
|---|---|---|---|
| Orca (`stablyai/orca`) | `0235673`, 2026-10-15 | MIT | Active. Desktop app. **Its source is public**: the brief listed it as closed, so it was read from code, not only from `docs/orca-feature-inventory.md`. |
| Conductor (`conductor.build`) | public docs, 2026-10-15 | closed | Active. Mac app. Read from its docs only. |
| Vibe Kanban (`BloopAI/vibe-kanban`) | `d5cbb53`, 2026-09-19 | Apache-2.0 | The README says it is shutting down. Web UI started by `npx`. |
| Claude Squad (`smtg-ai/claude-squad`) | `ce1ffb4`, 2026-08-20 | AGPL-3.0 | Active. Terminal UI over tmux. |
| Crystal (`stravu/crystal`) | `1e18e0b`, 2026-02-26 | MIT | Deprecated Feb 2026; replaced by Nimbalyst. Desktop app. |
| Nimbalyst (`Nimbalyst/nimbalyst`) | `ed76927`, 2026-10-14 | MIT | Active. Desktop app, Crystal's successor. |
| CloudCLI UI (`siteboon/claudecodeui`) | `7c3049f`, 2026-10-09 | AGPL-3.0 | Active. Web UI, self-hosted or their cloud. |
| opencode (`sst/opencode`, now `anomalyco/opencode`) | `4397ee4`, 2026-10-15 | MIT | Active. An agent of its own, not a wrapper of Claude Code. |

Handline is AGPL-3.0, so code from every open one may be borrowed with its notice kept: MIT and Apache-2.0 code can go into an AGPL-3.0 work, and AGPL-3.0 code is the same license. Conductor's code is not available; ideas only.

## Tools by step

What the user meets at each step. The sections below give the code behind each cell.

| Tool | Install, first launch | Account | Project | Folder trust | Team / agents | First work | Watch | Review | Merge |
|---|---|---|---|---|---|---|---|---|---|
| Orca | Homebrew cask or download; wizard: agent, theme, notifications, integrations, each skippable | Several Claude accounts, each its own folder; Orca runs `claude auth login` hidden, the browser opens | Add repo: local folder, clone, SSH host | **Writes `hasTrustDialogAccepted` into `.claude.json`** before each launch, under Claude's lock | Agent registry with a default; parallel and child worktrees | New worktree, pick an agent; or Start on an issue | Worktree cards with agent status from its hooks; terminal or chat view | Diff with inline comments sent to the agent as a prompt | PR and merge from the app |
| Conductor | Mac download; checks `gh auth status` and agent logins, walks you through what is missing | You run `claude /login` or `codex login` yourself | Open project, Open GitHub project, or Quick start (new repo) | Not in its docs | Claude Code, Codex, Cursor, OpenCode; several agents per workspace or one per workspace | A chat in a new workspace (from a branch, PR, issue or Linear issue) | Chat, Run button, terminal | Diff viewer (⌘⇧D), Checks tab (CI, comments, todos) | Create PR, merge, archive the workspace |
| Vibe Kanban | `npx vibe-kanban`; one screen: pick an agent, editor, sound, with a skip-permissions warning; then optional sign-in | None; it sees a login by `~/.claude.json` existing | Repo picker; a workspace is a worktree and branch | No dialog: Claude runs headless (`-p`) | One agent per workspace, switchable per follow-up | Issue on a board, then a workspace with a prompt | Log stream, terminal, dev server, built-in browser | Diff with inline comments sent back to the agent | Squash merge into base locally, or a PR |
| Claude Squad | Homebrew or install script; `cs` in a repo, no wizard | None; whatever `claude` is logged in | The current repo; a worktree per session | The user meets the dialog: its auto-dismiss has no caller | None; N sessions | `n` (name) or `N` (name and prompt) | Preview pane, or attach to the tmux session | Diff tab | `s` commits and pushes; PR by hand |
| Crystal | Download; one welcome modal with a skip-permissions warning and three steps | None; it checks the CLI exists | New folder (it runs `git init`) or an existing repo; a worktree per session | No dialog: headless `-p`, skip-permissions by default | None; N sessions per prompt | Session dialog: prompt, count, model, permission mode | Rendered message stream, terminal | Diff panel | Squash and rebase onto main |
| Nimbalyst | Download, bundles its own Claude; questionnaire, Standard or Developer mode | Login check through the SDK; login opens Terminal.app with "Type /login" | Open a folder; worktree per session | No dialog: Agent SDK, with in-app permission prompts | A lead agent spawns teammates | Agent chat; edits stream into open editors | Transcript and live editors | Changed files, file diffs, stage, commit | Merge, rebase, squash; PRs |
| CloudCLI UI | `npx` or their cloud; two steps: git identity (required), agent logins (optional) | Per-agent card opens an in-app shell running `claude --dangerously-skip-permissions /login` | Existing Claude projects, or a wizard: folder or GitHub clone | No dialog in chat (Agent SDK); the shell tab shows the CLI's own | One agent per chat, provider per session | Chat composer | Chat with tool cards, shell tab | Git panel: changes, history, diff | Commit (agent-written message), push; no PR |
| opencode | Install script or package managers, `opencode` in a folder; desktop app in beta | `opencode auth login`: pick a provider, then API key or browser OAuth | The current folder | Not Claude Code: its own per-agent permission rules | Build and Plan agents, General and Explore subagents | Prompt in the TUI | TUI stream of tool calls | Undo/redo a turn from git snapshots | None; the user commits |

## Per step: where it happens and what we could borrow

### 1. Install and first launch

- Orca: `src/renderer/src/components/onboarding/OnboardingFlow.tsx`, with steps agent (`AgentStep.tsx`), theme, notifications and integrations, and "skip to project setup" on every step but the last. `FeatureSetupInlineTerminal.tsx` installs a missing piece in an inline terminal.
- Vibe Kanban: `packages/web-core/src/features/onboarding/ui/LandingPage.tsx` is one screen of three columns, saving `onboarding_acknowledged`, `disclaimer_acknowledged` and the chosen agent. `OnboardingSignInPage.tsx` follows: GitHub, Google, local, or skip.
- Crystal: `frontend/src/components/Welcome.tsx`. Nimbalyst: `packages/electron/src/renderer/components/UnifiedOnboarding/UnifiedOnboarding.tsx`, persisted by `utils/onboardingCompletion.ts`. CloudCLI: `src/modules/onboarding/Onboarding.tsx`.
- Conductor: after install it checks for the tools and credentials it needs and walks you through what is missing (docs: Installation).

Borrow: one checklist screen that reads what is already there (the CLI on PATH, `gh auth status`, an agent login) and offers only what is missing, every step skippable (Orca, Conductor). Show the permission warning on that first screen, in one sentence, as Vibe Kanban and Crystal do, not later.

### 2. Connect an account

- Orca: `src/main/claude-accounts/claude-login-session.ts` runs a hidden `claude auth login` that writes the login straight into that account's own folder. The browser opens, or a sign-in link is given for a private window. Several accounts sit side by side (`claude-account-registration.ts`).
- Nimbalyst: `packages/electron/src/main/ipc/ClaudeCodeHandlers.ts:77` checks the login with an empty SDK `query()` and `accountInfo()`, which returns the email, organization and subscription type. `:162` opens Terminal.app (or cmd, or gnome-terminal) in the project folder, running its bundled claude with printed instructions: type `/login`, finish in the browser, type `/quit`.
- CloudCLI: `src/modules/provider-auth/ProviderLoginModal.tsx:47` runs `claude --dangerously-skip-permissions /login` in a shell inside the app; one card per agent (`AgentConnectionCard.tsx`).
- Vibe Kanban: `crates/executors/src/executors/claude.rs:601` only detects a login, from `~/.claude.json` existing. Claude Squad, Crystal, Conductor: the user logs in outside the tool.
- opencode: `packages/cli/src/commands/handlers/auth/login.ts` asks for a provider, then an API key or browser OAuth.

Borrow: Orca's hidden `claude auth login` into the account's own config folder fits our accounts model (one folder per account); it replaces telling the founder to run a command. Nimbalyst's `accountInfo()` check is a cheap way to say who an account is logged in as, before a launch fails on it.

### 3. Add a project

- Orca: `src/renderer/src/components/sidebar/AddRepoDialog.tsx` offers a local folder (`useAddRepoLocalFolderFlow.ts`), a clone (`useAddRepoCloneFlow.ts`), or a repo on an SSH host (`AddRepoHostSelector.tsx`). A worktree then starts from a chosen ref (inventory F07).
- Conductor: "Open project", "Open GitHub project", or "Quick start" for a new repo. A setup script brings in what git does not track (`.env`, dependencies), and a run script gets `CONDUCTOR_PORT`; both can be shared in `.conductor/settings.toml`.
- Crystal: a new folder gets `git init`. CloudCLI: `src/modules/project-creation-wizard/` (folder browser, GitHub clone). Vibe Kanban: `shared/components/CreateModeRepoPickerBar.tsx`, `FolderPickerDialog.tsx`.
- Claude Squad: `main.go:48` refuses to start outside a git repository.

Borrow: the three entry points, existing folder, clone and new repo, as one dialog. Conductor's setup script, run per new worktree and committed with the repo, covers the files a worktree lacks.

### 4. Folder trust

Two approaches, and only Orca of the terminal-based tools writes Claude's config:

- **Orca writes the trust.** `src/main/claude/claude-folder-trust-file.ts` (`grantClaudeWorkspaceTrust`) sets `projects[<folder>].hasTrustDialogAccepted = true` in the config Claude will read: `.claude.json` in `CLAUDE_CONFIG_DIR` or home, or a legacy `.config.json`, found per account (`:338`). Its care points:
  - it takes Claude's own lock file `<file>.lock` (`:244`) and renames a replacement made with the same file mode;
  - it never creates the file, and never rewrites one it could not parse;
  - it skips the lock when nothing would change;
  - it writes both the given path and its realpath (`:286`).
  `src/main/agent-workspace-trust.ts` (`applyAgentWorkspaceTrust`) calls it before every launch, with a short deadline, so a miss only means the agent asks. `execution-host-workspace-trust.ts` refuses to pre-trust a home or the root (`wouldTrustAHome`) and holds the same writers for Codex, Cursor and others. An SSH host writes on its own disk.
- **The rest avoid the dialog** by not running the interactive CLI. Vibe Kanban runs `npx -y @anthropic-ai/claude-code@2.1.119 -p` with stream-json (`crates/executors/src/executors/claude.rs:65` pins the version, `:159` adds `-p`), and skip-permissions is on by default (`crates/executors/default_profiles.json`). Crystal runs `-p` with stream-json (`main/src/services/panels/claude/claudeCodeManager.ts:66`, `:123`), skip-permissions by default (`:90`). Nimbalyst and CloudCLI use the Agent SDK, whose permission modes and `canUseTool` route prompts into the app. None of these four writes trust; Vibe Kanban, Nimbalyst and CloudCLI write `.claude.json` only to save MCP servers (Nimbalyst `packages/electron/src/main/services/MCPConfigService.ts:370` merges, keeping other keys).
- Claude Squad has `session/tmux/tmux.go:161` `CheckAndHandleTrustPrompt`, which reads the pane and presses Enter on "Do you trust the files in this folder?". At this commit nothing calls it, so the user meets the dialog on attach.
- Conductor's docs do not say how it handles trust.

Handline today: a structured session runs `--print`, so it meets no dialog; a terminal session's dialog is read off the screen and answered (`go/internal/term/press.go:448` `trustFolder`, `:478` `ConfirmTrust`).

Borrow: Orca's writer, for terminal sessions, in place of pressing keys on a screen. Write the trust of the agent's own worktree, before launch, into the config folder of the account it starts on, under Claude's lock, never creating or reformatting the file, and never for a home or the root. The code is MIT; the ideas carry over to Go directly.

### 5. Build a team or pick agents

- Orca: an agent registry with a default agent and flags per agent (inventory F31). Worktrees nest under a parent, and an orchestration skill hands work to child worktrees (F42). `AgentStep.tsx` lists the agents found on PATH first.
- Conductor: several agents in one workspace when they share a branch (one implements, one reviews), separate workspaces when the work becomes separate PRs.
- Nimbalyst: a lead agent spawns teammates through the SDK (`packages/runtime/src/ai/server/providers/TeammateManager.ts`).
- opencode: Build and Plan primary agents, General and Explore subagents with their own permission rules (`packages/core/src/plugin/agent.ts`, `plugin/plan.ts`).
- The others have no team: one agent per session, several sessions side by side.

Borrow: detect the installed agents and offer those first (Orca). Conductor's rule, one workspace per PR and agents share a workspace only when they share a branch, is a clear way to explain worktrees to a new user.

### 6. Give the first work

- Orca: a new worktree opens a terminal with an agent picker. The first-session guide races three agents on one task, one worktree each (onorca.dev/docs/first-session). An issue's Start makes a worktree linked to it (F48).
- Conductor: a workspace can start from a branch, a PR, a GitHub issue or a Linear issue; files, notes and screenshots can be mentioned or attached in the composer.
- Vibe Kanban: an issue on the board, then `crates/server/src/routes/workspaces/create.rs:212` `create_and_start_workspace`. Claude Squad: `N` takes a name and a prompt (`app/app.go`). Crystal: `CreateSessionDialog.tsx` runs one prompt N times to compare results.

Borrow: start from an issue or a PR as well as from typed text, and one-click "the same task in N worktrees" for a first look at parallel work.

### 7. Watch it

- Orca: worktree cards list each agent with its last message and a working or done mark, from hooks it installs into each agent's config (F05, F32), plus desktop notifications (F64).
- Vibe Kanban: a normalised stream of the agent's stream-json, with the dev server and a built-in browser beside it. Crystal and CloudCLI: the rendered message stream and a terminal. Claude Squad: a preview pane of the tmux screen (`ui/preview.go`).

Borrow: Orca's card, one line per agent with its last message and state, as the first thing a new user looks at.

### 8. Review

- Orca: a diff where a comment becomes a prompt to the agent (`src/renderer/src/components/diff-comments/diff-comment-zone-card.tsx`, `sendPrompt`). Vibe Kanban does the same (`packages/web-core/src/shared/hooks/useReview.ts`).
- Conductor: Diff Viewer (⌘⇧D) and a Checks tab for git status, CI, comments and todos.
- Claude Squad: `session/git/diff.go:26` against the base. Nimbalyst: `ipc/WorktreeHandlers.ts:961` changed files, `:992` a file's diff. opencode: git snapshots per turn, undone with `/undo` (`packages/core/src/session/revert.ts`).

Borrow: diff comments that go back to the agent as its next message; review stays in one place instead of a chat the founder writes by hand.

### 9. Merge

- Vibe Kanban: `crates/server/src/routes/workspaces/git.rs:179` calls `crates/git/src/lib.rs:575` `merge_changes`, a local squash into the base that is refused when the base has moved ahead. A rebase button (`git.rs:695`) and a PR with a written description (`routes/workspaces/pr.rs:188`) sit beside it.
- Crystal: `main/src/ipc/git.ts:902` squashes and rebases onto main. Nimbalyst: `worktree:merge`, `rebase`, `squash-commits` (`WorktreeHandlers.ts:1160`, `:1216`, `:1359`).
- Orca and Conductor: a PR, merged from the app (Orca `github-pr-merge-state.ts`, `PullRequestPage.tsx`); Conductor then archives the workspace.
- Claude Squad: `session/git/worktree_git.go:71` commits everything with `--no-verify` and pushes; the PR is made by hand. CloudCLI: commit with an agent-written message (`server/modules/git/git.routes.ts:1047`), then push. opencode: none.

Borrow: Vibe Kanban's refusal to merge when the base moved, with rebase one click away, and Conductor's archive of the workspace after the merge, so a finished task leaves nothing behind.

## Mobile agent apps (App Store screenshots)

`orca-references/apps` holds 56 App Store screenshots (`IMG_8418.PNG` to `IMG_8473.PNG`), each read for this section. `docs/mobile-apps-reference.md` (the phone app research) reads the same set for the phone app: 10 apps and 19 ideas. This section does not repeat those. It adds what the set says about first run and about the PC. As that doc found, no app's screenshots show its first run: the store sets open on the working app. What they show of the first steps is what each app promises a new user and how it starts the first piece of work.

| App | Files | Menu set | Sessions and chat | For first run or the PC |
|---|---|---|---|---|
| Moshi | 8418–8424 | Inbox / Usages switch | A Mosh terminal per session, kept through sleep, app exit and network change | "Back to every session" after a reconnect: the promise that nothing was lost |
| T3 Code | 8425–8430 | `…` menu; Environments sheet | One list across machines: repo, title, branch, machine, state, with an "Unsent: sends on reconnect" group | Machines are added by URL or through its relay. Each one is chosen as trusted to share PR data and GitHub access (8426). "Works with the agents you already use" (8425) |
| Happy | 8431–8435 | Sessions list, filter, settings | Bots with faces, sessions by project and branch, a team chat of people and agents | A desktop companion: a sidebar of Bots, then Projects with their branches under them (8434). "Use existing Claude, ChatGPT, Grok subscriptions" (8431). MIT licensed (8435) |
| Hermex | 8436–8443 | Bots, Tasks, Kanban, Skills, Memory, Usage, Active Profile, Projects, Scheduled sessions; Sessions with a Chat button | Chat with Thinking and tool-call rows; a stop button | The approval card says in one line what each scope covers: "Allow session covers every recursive delete in this session; Always allow covers it on this server from now on" (8438) |
| Railway | 8444–8448 | Bottom bar: overview, services, metrics, logs, chat | Chat that stages a change, then Apply | An empty home that says so plainly: "Zero notifications at the moment. Close the app, touch grass." (8444) |
| Agent-chat app (no name shown) | 8449–8451 | Menu button | Chat with Thinking and Shell rows; a Changes / Files / Terminals sheet with Commit | The turn ends with a report: commit, message, changed file, and "Unrelated change left untouched" (8449) |
| Codex client (no name shown) | 8452–8456 | Menu button | Chat; review findings as bug / warning / suggestion chips; Write tests / Refactor / Explain pills | Terminal tabs (Git, SSH) that say "reconnecting to the existing terminal session" (8456) |
| General AI assistant | 8457–8461 | Explore prompts, Chat, Detect, Recent | Plain chat | Nothing for agent work |
| Cursor (mobile) | 8462–8467 | All Workspaces: Pinned, Today, by repo | Rows with repo, Working / Passed / No Changes / Merged, +/− lines; a walkthrough video on a finished turn | A "Plan, ask, build…" box at the foot of the workspace list starts new work without a dialog. It names the repo and branch and picks the model (8462, 8463) |
| BotBus | 8468–8473 | Computers; a computer; New Task | Projects with their agent; chat with a "Wants to run a command" card | New Task is three choices: project, connector (agent), what to do (8469). Each project shows its agent. Pairing is many to many: one computer pairs with several phones (8472) |

### What they add to the first run and the PC

- **Say "bring your own subscription" on the first screen.** Happy and T3 Code both lead with it (8431, 8425); Orca's docs do too. For a new user it answers the first worry: no new account and no API key.
- **One box to start work.** Cursor, Happy and Hermex put a composer under the list ("Plan, ask, build…") that starts a session in a named repo and branch. BotBus's New Task asks only project, agent and what to do. Our first-work step should be that short.
- **A sidebar of agents above projects with their branches.** Happy's desktop companion (8434) is the PC layout of a team: bots on top, each project with its worktrees under it. It matches Orca's parent and child worktrees.
- **Explain a permission choice in one line where it is made.** Hermex's approval card (8438) does it for scopes. The first-run permission choice (Orca's Yolo mode, Vibe Kanban's and Crystal's notices) needs the same one line of what it allows.
- **Trust per machine, said once.** T3 Code asks which machines may share PR data and GitHub access (8426). When a second machine is added, that is the question to ask.
- **A calm empty state.** Railway's "Zero notifications at the moment" (8444): a new user's empty inbox should say nothing is waiting, not look broken.
- **An end-of-turn report that names what was left alone.** "Unrelated change left untouched" (8449) is the review step's first line of trust. Our task done evidence could carry the same line.
- **Say that a session survived.** Moshi (8418) and the Codex client (8456) say so when they reconnect. The PC app should say it too, after the daemon restarts or the laptop wakes.

