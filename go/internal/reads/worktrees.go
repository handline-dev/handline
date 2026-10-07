package reads

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
)

const (
	maxChanged   = 200
	maxDiffChars = 200_000
)

// git is rpc_worktrees._git: a read-only run, optional locks off.
func git(path string, args ...string) (bool, string) {
	r := gitrun.RunTimeout(path, 20*time.Second, append([]string{"--no-optional-locks"}, args...)...)
	return r.RC == 0, r.Stdout
}

func isDir(p string) bool { st, err := os.Stat(p); return err == nil && st.IsDir() }

func realpath(p string) string {
	if abs, err := filepath.Abs(p); err == nil {
		p = abs
	}
	if r, err := filepath.EvalSymlinks(p); err == nil {
		return r
	}
	return p
}

// repoOf is agents.repo_of: where the agent was told to work.
func (e *Env) repoOf(agent string) string {
	cfg, err := team.NewRoster(e.Home).Read(agent)
	if err != nil {
		return ""
	}
	s, _ := cfg["repo"].(string)
	return s
}

func defaultBranch(repo string) string {
	if ok, out := git(repo, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"); ok && strings.TrimSpace(out) != "" {
		return strings.TrimPrefix(strings.TrimSpace(out), "origin/")
	}
	return gitrun.IntegrationBranch
}

// repos is rpc_worktrees.repos: the repos the agents were told to work in, by path.
func (e *Env) repos() []any {
	seen := map[string]bool{}
	var paths_ []string
	for _, name := range paths.ListAgents(e.Home) {
		if repo := e.repoOf(name); repo != "" && !seen[repo] && isDir(repo) {
			seen[repo] = true
			paths_ = append(paths_, repo)
		}
	}
	sort.Strings(paths_)
	out := make([]any, 0, len(paths_))
	for _, p := range paths_ {
		out = append(out, map[string]any{"name": filepath.Base(p), "path": p, "default_branch": defaultBranch(p)})
	}
	return out
}

func worktreePath(recorded string) string {
	if recorded == "" {
		return ""
	}
	p := recorded
	if i := strings.Index(recorded, "::"); i >= 0 {
		p = recorded[i+2:]
	}
	if isDir(p) {
		return p
	}
	return ""
}

// known is a dict in Python's sense: later writes to a key keep its first place.
type known struct {
	who   map[string]string
	order []string
}

func (k *known) put(path, agent string) {
	if _, has := k.who[path]; !has {
		k.order = append(k.order, path)
	}
	k.who[path] = agent
}

func jsonList(text string) ([]any, bool) {
	if text == "" {
		return nil, true
	}
	var v any
	if json.Unmarshal([]byte(text), &v) != nil {
		return nil, false
	}
	l, ok := v.([]any)
	return l, ok
}

// knownWorktrees is path -> the agent that works there, for every agent whose recorded worktree exists.
func (e *Env) knownWorktrees() (known, error) {
	out := known{who: map[string]string{}}
	for _, name := range paths.ListAgents(e.Home) {
		row, err := queryOne(e.DB, "SELECT worktree, extra_worktrees FROM agent_state WHERE agent = ?", name)
		if err != nil {
			return known{}, err
		}
		var all []string
		if p := worktreePath(row.str("worktree")); p != "" {
			all = append(all, p)
		}
		if listed, ok := jsonList(row.str("extra_worktrees")); ok {
			for _, x := range listed {
				if s, ok := x.(string); ok {
					if p := worktreePath(s); p != "" {
						all = append(all, p)
					}
				}
			}
		}
		for _, p := range all {
			out.put(realpath(p), name)
		}
	}
	return out, nil
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func countOf(s string) int64 {
	if !isDigits(s) {
		return 0
	}
	n, _ := strconv.ParseInt(s, 10, 64)
	return n
}

// changed is rpc_worktrees._changed: the changed files with git's status letter and line counts.
func changed(path string) []any {
	ok, status := git(path, "status", "--porcelain=v1", "-z", "-uall")
	if !ok {
		return []any{}
	}
	counts := map[string][2]int64{}
	if ok, stat := git(path, "diff", "--numstat", "-z", "--no-ext-diff", "HEAD"); ok {
		parts := strings.Split(stat, "\x00")
		for i := 0; i < len(parts); i++ {
			head := parts[i]
			a, rest, _ := strings.Cut(head, "\t")
			d, name, _ := strings.Cut(rest, "\t")
			if name == "" && i+2 < len(parts) && strings.Count(head, "\t") == 2 {
				name, i = parts[i+2], i+2
			}
			if head != "" {
				counts[name] = [2]int64{countOf(a), countOf(d)}
			}
		}
	}
	rows := []any{}
	entries := strings.Split(status, "\x00")
	for i := 0; i < len(entries) && len(rows) < maxChanged; i++ {
		entry := entries[i]
		if len([]rune(entry)) < 4 {
			continue
		}
		code, name := entry[:2], entry[3:]
		if strings.ContainsAny(code, "RC") {
			i++
		}
		mark := "M"
		if code == "??" {
			mark = "?"
		} else {
			for _, c := range "RAD" {
				if strings.ContainsRune(code, c) {
					mark = string(c)
					break
				}
			}
		}
		n := counts[name]
		rows = append(rows, map[string]any{"path": name, "status": mark, "added": n[0], "deleted": n[1]})
	}
	return rows
}

// bosses is each agent's supervisor from team.yaml; empty when it cannot be read.
func (e *Env) bosses() map[string]string {
	t, err := team.LoadTeam(e.Home)
	if err != nil {
		return map[string]string{}
	}
	out := map[string]string{}
	for _, a := range paths.ListAgents(e.Home) {
		out[a] = t.SupervisorOf(a)
	}
	return out
}

func (e *Env) worktree(path, owner string, kn known, bosses map[string]string) map[string]any {
	ok, branch := git(path, "rev-parse", "--abbrev-ref", "HEAD")
	ch := changed(path)
	boss := bosses[owner]
	repo := e.repoOf(owner)
	repoName := ""
	if repo != "" {
		repoName = filepath.Base(repo)
	}
	var parent any
	children := []string{}
	for _, p := range kn.order {
		o := kn.who[p]
		if boss != "" && o == boss && parent == nil {
			parent = p
		}
		if bosses[o] == owner {
			children = append(children, p)
		}
	}
	sort.Strings(children)
	status := "clean"
	if len(ch) > 0 {
		status = "dirty"
	}
	b := ""
	if ok {
		b = strings.TrimSpace(branch)
	}
	return map[string]any{"path": path, "repo": repoName, "branch": b, "owner": owner, "parent": parent, "children": children,
		"status": status, "changed": ch}
}

// throughALink is rpc_worktrees._through_a_link: whether file is, or passes through, a symbolic link under root, or resolves
// outside it.
func throughALink(root, file string) bool {
	cur := root
	for _, part := range strings.Split(file, "/") {
		if part == "" || part == "." {
			continue
		}
		cur = filepath.Join(cur, part)
		if st, err := os.Lstat(cur); err == nil && st.Mode()&os.ModeSymlink != 0 {
			return true
		}
	}
	real := realpath(cur)
	return real != root && !strings.HasPrefix(real, root+"/")
}

func (e *Env) reposList(_ *rpc.Conn, _ map[string]any) (any, error) {
	return map[string]any{"repos": e.repos()}, nil
}

func (e *Env) worktreesList(_ *rpc.Conn, params map[string]any) (any, error) {
	var repo, owner *string
	for _, f := range []struct {
		k string
		p **string
	}{{"repo", &repo}, {"owner", &owner}} {
		v, err := optStr(params, f.k)
		if err != nil {
			return nil, badParam(f.k+" is a string", f.k)
		}
		*f.p = v
	}
	kn, err := e.knownWorktrees()
	if err != nil {
		return nil, err
	}
	bosses := e.bosses()
	keys := append([]string(nil), kn.order...)
	sort.Strings(keys)
	out := []any{}
	for _, p := range keys {
		who := kn.who[p]
		wt := e.worktree(p, who, kn, bosses)
		if (owner == nil || who == *owner) && (repo == nil || *repo == wt["repo"] || *repo == e.repoOf(who)) {
			out = append(out, wt)
		}
	}
	return map[string]any{"worktrees": out}, nil
}

func (e *Env) worktreeDiff(_ *rpc.Conn, params map[string]any) (any, error) {
	path, _ := params["path"].(string)
	if path == "" {
		return nil, badParam("path is a worktree path", "path")
	}
	var file string
	hasFile := false
	if v, has := params["file"]; has && v != nil {
		s, ok := v.(string)
		if !ok || s == "" || strings.HasPrefix(s, "/") || strings.HasPrefix(s, "-") || strings.ContainsRune(s, 0) {
			return nil, badParam("file is a path inside the worktree", "file")
		}
		for _, part := range strings.Split(s, "/") {
			if part == ".." {
				return nil, badParam("file is a path inside the worktree", "file")
			}
		}
		file, hasFile = s, true
	}
	kn, err := e.knownWorktrees()
	if err != nil {
		return nil, err
	}
	root := realpath(path)
	if _, ok := kn.who[root]; !ok {
		nf := rpc.NotFoundError(path + " is not a worktree an agent works in")
		nf.Data = map[string]any{"what": "worktree", "path": path}
		return nil, nf
	}
	if hasFile && throughALink(root, file) {
		return nil, badParam("file goes through a link: only what lies inside the worktree is read", "file")
	}
	args := []string{"diff", "--no-ext-diff", "--no-textconv", "--no-color", "HEAD"}
	if hasFile {
		args = append(args, "--", file)
	}
	_, out := git(root, args...)
	if hasFile && out == "" {
		full := filepath.Join(root, file)
		if st, err := os.Lstat(full); err == nil && st.Mode().IsRegular() {
			if tracked, _ := git(root, "ls-files", "--error-unmatch", "--", file); !tracked {
				_, out = git(root, "diff", "--no-index", "--no-ext-diff", "--no-textconv", "--no-color", "--", os.DevNull, file)
			}
		}
	}
	shown, truncated := out, false
	if len(out) > maxDiffChars {
		if r := []rune(out); len(r) > maxDiffChars {
			shown, truncated = string(r[:maxDiffChars]), true
		}
	}
	var fileOut any
	if hasFile {
		fileOut = file
	}
	return map[string]any{"path": path, "file": fileOut, "diff": shown, "truncated": truncated}, nil
}
