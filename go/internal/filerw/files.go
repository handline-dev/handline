package filerw

import (
	"fmt"
	"os"
	"sort"
	"strings"

	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/rpc"
)

// listDefault and listMax are files.list's limit when none is given and the most it takes.
const (
	listDefault = 50
	listMax     = 200
)

// list is files.list: what git tracks plus the untracked files it does not ignore, the regular files among them, best matches first.
func (e *Env) list(_ *rpc.Conn, p map[string]any) (any, error) {
	agent, _ := p["agent"].(string)
	if agent == "" {
		return nil, bad("agent is an agent's name", "agent")
	}
	query := ""
	if v, has := p["query"]; has && v != nil {
		s, ok := v.(string)
		if !ok {
			return nil, bad("query is a string", "query")
		}
		query = strings.ToLower(s)
	}
	limit := listDefault
	if v, has := p["limit"]; has && v != nil {
		n, ok := rpc.AsInt(v)
		if !ok || n < 1 || n > listMax {
			return nil, bad(fmt.Sprintf("limit is an integer from 1 to %d", listMax), "limit")
		}
		limit = int(n)
	}
	wt, err := e.worktree(agent)
	if err != nil {
		return nil, err
	}
	root, err := os.OpenRoot(wt)
	if err != nil {
		return nil, rpc.NotFoundError("the worktree of " + agent + " is gone")
	}
	defer root.Close()
	r := gitrun.Run(wt, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
	if r.RC != 0 {
		return nil, fmt.Errorf("git ls-files in %s: %s", wt, strings.TrimSpace(r.Stderr))
	}
	type hit struct {
		path string
		tier int
	}
	var hits []hit
	seen := map[string]bool{}
	for _, rel := range strings.Split(r.Stdout, "\x00") {
		if rel == "" || seen[rel] { // a conflicted file is listed once per stage
			continue
		}
		seen[rel] = true
		if tier, ok := match(rel, query); ok {
			hits = append(hits, hit{rel, tier})
		}
	}
	sort.Slice(hits, func(i, j int) bool {
		a, b := hits[i], hits[j]
		if query != "" && a.tier != b.tier {
			return a.tier < b.tier
		}
		if query != "" && len(a.path) != len(b.path) {
			return len(a.path) < len(b.path)
		}
		return a.path < b.path
	})
	// Only the files returned, and one to tell whether more follow, are looked at on disk.
	files, more := []any{}, false
	for _, h := range hits {
		fi, err := root.Lstat(h.path)
		if err != nil || !fi.Mode().IsRegular() { // deleted since it was tracked, a link, or a submodule
			continue
		}
		if len(files) == limit {
			more = true
			break
		}
		files = append(files, map[string]any{"path": h.path, "size": fi.Size()})
	}
	return map[string]any{"files": files, "more": more}, nil
}

// match is whether the lowercased query q is a subsequence of path, compared lowercased, and how good the match is: 0 inside the file's
// name, 1 elsewhere in the path, 2 spread out. An empty query matches everything.
func match(path, q string) (int, bool) {
	if q == "" {
		return 0, true
	}
	lower := strings.ToLower(path)
	if strings.Contains(lower[strings.LastIndex(lower, "/")+1:], q) {
		return 0, true
	}
	if strings.Contains(lower, q) {
		return 1, true
	}
	rest := []rune(q)
	for _, c := range lower {
		if len(rest) > 0 && c == rest[0] {
			rest = rest[1:]
		}
	}
	return 2, len(rest) == 0
}
