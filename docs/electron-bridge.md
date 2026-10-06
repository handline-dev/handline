# Electron bridge: the one API between the web app and the shell

`apps/electron` shows the built `apps/web` in one window. Two things must cross the window's edge: what the page knows and the shell does not (which agent is on screen, whether a draft is open), and what the shell can do and a page cannot (open a file, run an editor). This is that surface, written before E3 so the web units (U8, U10) and the shell build to the same names. Read against `apps/electron` @d7f19418 (E1, E2 and E4).

## 1. What exists at d7f19418

**There is no preload and no API.** Badge, notices and updates all run in the main process on its own daemon connection (the Unix socket, `desk.ts`); the page does not know the shell is there. Two places reach into the page by `executeJavaScript`, and both are replaced by calls below:

| In `main.ts` / `update.ts` / `notices.ts` | What it does | Replaced by |
|---|---|---|
| `location.hash = '#/agents/<name>'` on a notice click | lands on the agent's workspace | `onNavigate` (§3.4) |
| `agentOfHash(hash)`, regex `^#/agents/(.+)$` | decides "that agent is on screen, no notice" | `screen` (§3.1); the regex would also take `x/y` from any deeper route |
| `UNSENT_PROBE`: a DOM scan for `dialog[open]`, filled text fields, `[data-id^="-"]`, `[data-pending]` | decides "nothing would be lost by a restart" | `unsent` (§3.2) |

The window is already locked down (`setWindowOpenHandler` sends http/https to the browser, `will-navigate` keeps the loopback origin); the web preferences are Electron's defaults, which this document tightens (§2).

## 2. The surface

One object, `window.handline`, made by a preload with `contextBridge`. No other global, no generic `invoke(channel, ...)`, no Node in the page.

- The window sets `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`.
- Each call is one `ipcRenderer.invoke` on its own channel. The main side checks **where it came from** (`event.senderFrame` is the window's top frame and its origin is the loopback origin `serveDir` returned) and **what it was given** (the exact keys and types below; an unknown key, a wrong type or an over-long string is `bad_request`) before it does anything.
- **Every call returns a result, never throws**: `{ ok: true, ... }` or `{ ok: false, code, message }`. Errors do not cross `contextBridge` with their type, and the page must not parse a message to decide. `message` is a sentence for the person.
- **Detection.** `window.handline?.api === 1` says the shell is there. Each call is present only if this shell has it (`typeof handline.compose === 'function'`); the page checks the call it is about to use, never a version. New calls only add, so `api` stays 1; a change that breaks one makes it 2.
- The types are one file, `packages/app-core/src/shell.ts` (types and the pure `routeOfTarget`, no DOM), imported by the preload and by `apps/web/src/shell.ts`, which holds the only reads of `window.handline`. Views get it through that module; a view that touches `window.handline` is a defect.

| Call | From | Shell has it at | Plain browser |
|---|---|---|---|
| `screen` | page → shell | E1 | no call, nothing to do |
| `unsent` | page → shell | E4 | no call; a reload asks nothing |
| `onNavigate` | shell → page | E2 | none; a deep link is the URL itself |
| `openPath`, `cancelCompose`, `compose` | page → shell | E3 | §4 |

## 3. Badge, notices, update, and the notice's click

The badge and the notices need **no call**: the shell reads `daemon.status` and the `notifications` topic itself and the page is not involved. Written here so nobody adds one.

| | Shell | Plain browser |
|---|---|---|
| Badge | the dock badge shows `inbox.unread` (`99+` above 99; Linux count only, none on Windows) | the bell count in the app's top bar, and `(n) ` in front of the tab title; the page does both in the shell too |
| Notice | one native notice per `prompt_asked`, `task_done`, `stall`, `account_out`; an HR proposal has no event yet, so none | none: no permission prompt, no `Notification` API. The bell and the cards are the signal |
| Update | "Handline was updated. Restart now?" on a newer build, once idle; "Not now" is not asked again for that build | none: a new deploy is a reload |

### 3.1 `screen`

```ts
screen(s: { agent: string | null }): Promise<{ ok: true }>
```

The page calls it when the agent whose workspace is on screen changes (route change, or no agent selected: `null`). The shell keeps the last value and raises no notice for an event of that agent while the window is focused (`suppressed`). Before the first call the shell treats the value as `null`. Errors: `bad_request`. This replaces the shell's own parse of the hash, so a route may grow without the shell knowing.

### 3.2 `unsent`

```ts
unsent(on: boolean): Promise<{ ok: true }>
```

`true` while a restart would lose something: a box (dialog, modal) is open, a compose box holds text, or a write is in flight or pending (`useIsMutating() > 0`, a `pending` mark, the Terminal input holding text). The page calls it when the value changes. The shell asks "Restart now?" only while the last value is `false`; a build owed is asked at the next check once it turns `false`. **Until the page has called it once, the shell keeps using `UNSENT_PROBE`**; the probe is deleted in the commit after U8 and U10 call `unsent`. A draft the page persists (U8 keeps one per agent) does not count as unsent: a restart keeps it. Errors: `bad_request`.

### 3.3 The deep link

The daemon already puts one on each event: `Event.target = { screen, id }` (`stream.py` emits `agent`, `task` and `account`). The contract types `screen` as a free string, so **this table is the vocabulary**; a screen the page does not know is ignored (the window is still shown).

| Event | `target` | The page lands on |
|---|---|---|
| `prompt_asked` | `agent`, the agent's name | `#/agents/<name>`, the Chat tab focused with the open prompt's card in view (U9). The prompt is gone by then: the chat opens with no card, which is right |
| `task_done` | `task`, the task id | `#/inbox`, the card of that task's result in view (U10); none left (read, dismissed): `#/tasks/<id>` (U11) |
| `stall` | `agent` | `#/agents/<name>` |
| `account_out` | `account`, the account name | `#/accounts/<name>` (U14) |

`routeOfTarget(target, eventType) → string | null` in `app-core` is the only table; a click in the app on an inbox row uses it too. Ids are `encodeURIComponent`ed. The route strings belong to the web units; this table changes with them.

### 3.4 `onNavigate`

```ts
onNavigate(cb: (to: { screen: string; id: string; event: string }) => void): () => void   // returns off
```

Called when a notice is clicked (`event` is the notifications type, `screen` and `id` the event's `target`). The shell has already shown and focused the window. **A click before the page registered is not lost**: the preload keeps the last one and delivers it to the first `onNavigate`. A notice whose event has no `target` does not call it. Replaces `executeJavaScript('location.hash = ...')`.

## 4. E3: open a path, compose in an editor

Both are for Chat (U8) and Inbox (U10). **Both are a convenience on top of something the page already does**, so a shell without them, or a refusal, never leaves the person stuck.

### 4.1 `openPath`

```ts
openPath(a: { path: string; agent?: string }): Promise<
  | { ok: true; how: 'opened' | 'revealed' }
  | { ok: false; code: 'bad_request' | 'not_found' | 'unavailable'; message: string } >
```

`path` is what the message holds, `~/x`, `/abs/x`, or relative to the agent's worktree (`agent` names it); the page strips a `:42` or `:42:7` and drops it (the default app has no use for it). The page links a span of text only for the shapes the bridge's `find_paths` links: `~/...` and an absolute path with an extension, always; a relative one only if it holds a `/` or an extension. It does not check the disk. The shell does, so a miss is `not_found` on the click and the text stays plain.

**Safety rules** (the shell enforces all of them; the page's checks are for display):

1. **A path, nothing else.** Under 4096 characters, no NUL, no `scheme:` prefix (`file:`, `http:`, `smb:`), no control characters. `openExternal` is never called with it.
2. **Resolved by the shell, from trusted data.** `~/` is the shell user's home. A relative path is joined to the agent's `worktree.path`, which the shell reads from `agent.get` on its own daemon connection, never from the page; an agent with no worktree, or a path whose `..` leaves it, is `not_found`. The bridge also tried the repo root; the contract has no field for it, so it is left out until asked for.
3. **Judged after `realpath`.** The resolved path must exist; what it points to, through symlinks, is what is judged, so a link to `/usr/bin/x` inside a worktree is judged as `/usr/bin/x`.
4. **A thing that runs is revealed, not opened.** A directory, or a regular file with no execute bit and an extension not in the list below, goes to `shell.openPath` (the default app; a directory opens in the file manager): `how: 'opened'`. Anything else goes to `shell.showItemInFolder`: `how: 'revealed'`, and the person decides. The list: `app command tool terminal workflow action scpt scptd applescript sh bash zsh fish csh ksh pl rb py js mjs jar pkg mpkg dmg iso exe bat cmd com msi scr ps1 vbs lnk url webloc inetloc fileloc desktop appimage run`; a directory named `*.app` is in it. The list is a minimum: a file the OS would run when opened is a defect to add.
5. **Where it is does not matter, what it is does.** Agents and the founder are one OS user, so a path in `/etc` or `/var/log` opens as it would from the Finder; no allowed-roots list (it would refuse `/tmp/out.log` and protect nothing that rule 4 does not).
6. **Only on a click.** The page calls it from a user event handler, never from rendering, an effect or a timer; the shell cannot check this, so U8 and U10 tests assert it.
7. Nothing is run with a shell; `shell.openPath` and `showItemInFolder` are the only openers. No `child_process` here.

`unavailable`: the OS refused or has no opener (`openPath` returned an error string); the message carries it.

**Plain browser:** a path stays plain, selectable text (code style). No link, no click. A path the page cannot open is not a loss; the person can copy it.

### 4.2 `compose` and `cancelCompose`

```ts
compose(a: { text?: string; ext?: 'md' | 'txt' }): Promise<
  | { ok: true; text: string; changed: boolean }
  | { ok: false; code: 'bad_request' | 'no_editor' | 'terminal_editor' | 'bad_editor' | 'not_waiting'
                      | 'editor_failed' | 'cancelled' | 'busy' | 'too_large' | 'unavailable'; message: string } >
cancelCompose(): Promise<{ ok: true }>
```

"Answer in editor" and "write in editor": `text` is what the box holds now (up to 256 KiB; default empty), `ext` the file's extension (default `md`). The promise settles when the editor exits; the page shows its box as "waiting for the editor" with a **Cancel** that calls `cancelCompose` (the compose then settles `cancelled`). The page **never clears its own box** until it gets `ok: true`, so every failure leaves the person's draft where it was, and the box stays usable the whole time. `changed` is false when the file holds what it was given; the page treats that as "nothing to send". The page trims trailing newlines (editors add one).

**Which editor.** `$VISUAL`, then `$EDITOR`. A shell launched from the Dock or a launcher does not inherit them, so when neither is set in the shell's own environment the shell asks the person's login shell once at launch, `$SHELL -ilc` printing both between markers (the rc files may print too), with a 3 s limit, and keeps the answer. Neither: `no_editor`, and the message says to set `VISUAL`.

**Safety rules:**

1. **No shell.** The editor string is split into words by the shell's own small parser: spaces separate, single and double quotes and a backslash group; **no expansion and no operators**. A `; | & < > $` backtick `(` `)` or a newline outside quotes is `bad_editor`, and the string is never given to `/bin/sh`. The first word is looked up on `PATH` by the shell (or taken as is when absolute) and must be a regular file with an execute bit; the rest are arguments, and the temp file's path is appended as the **last** argument. It is started with `child_process.spawn(file, args, { shell: false, stdio: 'ignore', cwd: <the temp directory> })`. The message text is never on the command line, in the environment, or in an argument.
2. **A terminal editor cannot run here** (the child has no tty): `vi vim nvim nano pico emacs micro hx helix joe ne` and `emacs -nw`, by the first word's basename, are `terminal_editor`, with a message naming a GUI editor that waits (`code --wait`, `subl -w`, `zed --wait`). The page falls back to its own box, which it always has.
3. **It must wait.** A GUI editor that returns at once (`code` without `--wait`) is detected as exit 0 in under 1.5 s with the file unchanged: `not_waiting`, and the message names the wait flag. Exit non-zero: `editor_failed` (the text is discarded, not returned).
4. **The temp file.** `fs.mkdtemp` under `app.getPath('temp')`, a directory `handline-<random>` of mode 0700, holding one file `compose.<ext>` created `wx` with mode 0600 and written with `text`. Never the repository, never a name the page chose. Read back whole as UTF-8, at most 256 KiB (`too_large` above), after the editor exits.
5. **Lifetime.** The directory is removed (recursively, `finally`) when the compose settles by any path, by cancel (the child is killed first), and on `quit` (child killed, directory removed). At launch the shell removes any `handline-*` directory in the temp directory older than a day that it owns (a crash). One compose at a time per shell: a second is `busy`; a second window is not a thing here.
6. **Only from the page that asked.** The sender check of §2 applies; the editor runs as the shell's own user with the shell's environment, nothing more.

**Plain browser:** there is no editor to run; the page offers its own multi-line box in the same place (a larger textarea in a dialog, the compose box's own keys). "In editor" appears only when `typeof handline.compose === 'function'`, and a `terminal_editor`, `no_editor` or `not_waiting` result turns the same dialog on with the text kept.

## 5. Tests

The shell tests run under node with the Electron side injected, as `Desk` and `Updater` already are.

1. `openPath`: each rule 1–5 with a fixture tree (a symlink out of the worktree to an executable, `..` out of it, `x.sh`, `x.app/`, a plain `.md`, a directory, a missing file, `file:///etc/passwd`); `bad_request` for an extra key; a call from a frame of another origin is refused.
2. `compose`: a fake editor script that appends a line (ok, `changed`), one that exits at once (`not_waiting`), one that exits 1, `vim` (`terminal_editor`), an editor string with `;` and one with a quoted space in the path; `cancelCompose` kills the child; the directory is gone after each; a second call is `busy`. The temp file's mode is 0600 and its directory 0700.
3. `screen` suppression and `unsent` gating at the `Desk` and `Updater` ports; the probe is used only before the first `unsent`.
4. `onNavigate`: a click before registration is delivered once to the first listener; no `target`, no call.
5. Web (happy-dom, no `window.handline`): no "In editor" button, a path is plain text, `shell.ts` returns `null`; with a stub `window.handline`, the same views call it as above.

## 6. Open points

- The route strings in §3.3 are the web units' to fix (U10, U11, U14); change the table, not the shell.
- An HR proposal notice needs a `notifications` event the contract does not have; add a row here when it does.
- `terminal_editor` is the honest limit of a GUI app. Running the editor in the Terminal pane (a pty on the daemon) would lift it; it is a feature, not a fix, and not planned.
