<!-- Generated from contract/spec/ by contract/generate.py; do not edit. -->
# repos methods (protocol 1)

A method marked write takes a required `request_id`. Framing, roles, errors and gates are in [protocol.md](../protocol.md); field types in `protocol.ts`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `repos.list` | member |  | none | `{repos}` |  |  |
| `worktrees.list` | member |  | `repo?`, `owner?` | `{worktrees}` |  |  |
| `worktree.diff` | member |  | `path`, `file?` | `{path, file, diff, truncated}` | `not_found` |  |
| `files.list` | member |  | `agent`, `query?`, `limit?` | `{files, more}` | `bad_request`, `not_found` |  |
| `file.read` | founder |  | `agent`, `path` | `{text, size, truncated, mtime, etag}` | `bad_request`, `not_found`, `refused` |  |
| `file.write` | founder | yes | `agent`, `path`, `text`, `etag`, `request_id` | `{size, mtime, etag}` | `bad_request`, `not_found`, `refused` | denied, stale, too_big |
| `worktree.set_status` | member | yes | `path`, `status`, `request_id` | `Accepted` | `not_found`, `bad_request` |  |

### `repos.list`

Repos.

### `worktrees.list`

Worktrees with status, changed files, what each holds beyond its base and its pull request. The worktrees agents work in, each with what it holds that main does not and its size. `repo` narrows them, by the repo's name or path.

### `worktree.diff`

Read-only git diff run by the daemon. Without `file`: the changes to tracked files against HEAD, then a new-file diff of each untracked file that .gitignore does not ignore (at most 200; one too big for what is left of the size cap is listed as `(new file, N bytes: not shown)`, not diffed). With `file`: that file alone, untracked or not.

### `files.list`

The files of an agent's worktree, for the chat's @ menu. The files are what git tracks plus the untracked files it does not ignore (`git ls-files --cached --others --exclude-standard`), the regular files among them that are there now. An agent with no worktree, or whose worktree is gone, is not_found. It reads only names and sizes, never a file's content.

### `file.read`

The founder reads a text file in an agent's worktree.

### `file.write`

The founder replaces or creates a .md or .json file in an agent's worktree, if it is still what the etag says.

### `worktree.set_status`

Set the status label of a worktree. The status is a free string (Worktree.status), 1 to 40 characters; the label changes nothing on disk, and worktrees.list shows it in place of the daemon's own word (dirty or clean). A path that is not a worktree an agent works in is not_found, what worktree, as for worktree.diff.

