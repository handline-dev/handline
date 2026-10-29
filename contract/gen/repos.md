<!-- Generated from contract/spec/ by contract/generate.py; do not edit. -->
# repos methods (protocol 1)

A method marked write takes a required `request_id`. Framing, roles, errors and gates are in [protocol.md](../protocol.md); field types in `protocol.ts`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `repos.list` | member |  | none | `{repos}` |  |  |
| `worktrees.list` | member |  | `repo?`, `owner?` | `{worktrees}` |  |  |
| `worktree.diff` | member |  | `path`, `file?` | `{path, file, diff, truncated}` | `not_found` |  |
| `worktree.remove` | founder | yes | `path`, `force?`, `request_id` | `Accepted & {branch_deleted}` | `refused`, `bad_request` | orphan, dirty, git |
| `worktree.declare` | agent | yes | `path`, `request_id` | `Accepted` | `refused`, `bad_request`, `not_found` | role |
| `file.read` | founder |  | `agent`, `path` | `{text, size, truncated, mtime, etag}` | `bad_request`, `not_found`, `refused` |  |
| `file.write` | founder | yes | `agent`, `path`, `text`, `etag`, `request_id` | `{size, mtime, etag}` | `bad_request`, `not_found`, `refused` | denied, stale, too_big |
| `worktree.set_status` | member | yes | `path`, `status`, `request_id` | `Accepted` | `not_found`, `bad_request` |  |

### `repos.list`

Repos.

### `worktrees.list`

Worktrees with status and changed files. The worktrees agents work in, then the orphans: worktrees of the same repos that no agent records (orphan true), each with what it holds that main does not and its size. `owner` lists no orphans, since they have none; `repo` narrows both, by the repo's name or path.

### `worktree.diff`

Read-only git diff run by the daemon. Without `file`: the changes to tracked files against HEAD, then a new-file diff of each untracked file that .gitignore does not ignore (at most 200; one too big for what is left of the size cap is listed as `(new file, N bytes: not shown)`, not diffed). With `file`: that file alone, untracked or not.

### `worktree.remove`

Remove an orphan worktree (worktrees.list orphan), the founder's choice from the list. A worktree merged into main with nothing changed goes at once, with its branch. One that holds work is refused, gate `dirty`, with `files_changed` and `commits_ahead` in the error data, unless force. A path that is no orphan (an agent works in it, or it is not a worktree of an agent's repo) is refused, gate `orphan`: an agent's worktree goes only with the agent. If git cannot remove it, the call is refused, gate `git`, with git's words.

### `worktree.declare`

The calling agent records a worktree it works in, beside the one its session started in. Kept in agent_state.extra_worktrees, so worktrees.list shows it as the agent's, never as an orphan, and it goes with the agent when the agent is retired. An agent that works in a worktree other than the one its session started in (by `git -C`) records it with `hl worktree add`. A path that is no worktree of an agent's repository, or is the repository's own checkout, is not_found. One the agent already records is accepted and said so. Only an agent calls it: a person is refused, gate `role`.

### `file.read`

The founder reads a text file in an agent's worktree.

### `file.write`

The founder replaces or creates a .md or .json file in an agent's worktree, if it is still what the etag says.

### `worktree.set_status`

Set the status label of a worktree. The status is a free string (Worktree.status), 1 to 40 characters; the label changes nothing on disk, and worktrees.list shows it in place of the daemon's own word (dirty or clean). A path that is not a worktree an agent works in is not_found, what worktree, as for worktree.diff.

