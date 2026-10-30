package filerw

import (
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"testing"
)

func (r *rig) gitOut(args ...string) ([]byte, error) {
	cmd := exec.Command("git", args...)
	cmd.Dir = r.wt
	cmd.Env = append(os.Environ(), "GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com")
	return cmd.CombinedOutput()
}

func (r *rig) git(args ...string) {
	r.t.Helper()
	if out, err := r.gitOut(args...); err != nil {
		r.t.Fatalf("git %v: %v %s", args, err, out)
	}
}

// gitRig is the rig with its worktree a git repository: tracked, untracked, ignored, a tracked file deleted since and a link.
func gitRig(t *testing.T) *rig {
	t.Helper()
	r := newRig(t)
	git := r.git
	git("init", "-q", "-b", "main")
	for rel, text := range map[string]string{".gitignore": "build/\n", "README.md": "hello\n", "src/app/Main.tsx": "x", "gone.md": "x",
		"src/my_animation.ts": "x", "main/run.sh": "x", "lib/main.go": "x", "docs/domain.md": "x",
		"main/deep/nested/tool.sh": "x"} {
		r.put(rel, text)
	}
	git("add", "-A")
	git("commit", "-q", "-m", "first")
	os.Remove(filepath.Join(r.wt, "gone.md"))
	r.put("notes/todo.md", "x")
	r.put("build/out.js", "x")
	if err := os.Symlink("README.md", filepath.Join(r.wt, "link.md")); err != nil {
		t.Fatal(err)
	}
	return r
}

func paths(t *testing.T, res map[string]any) []string {
	t.Helper()
	var out []string
	for _, f := range res["files"].([]any) {
		out = append(out, f.(map[string]any)["path"].(string))
	}
	return out
}

func TestFilesListsTrackedAndUntrackedFilesNeverIgnoredOnesBestMatchesFirst(t *testing.T) {
	r := gitRig(t)
	res := result(t, r.call("files.list", map[string]any{"agent": "alice"}))
	want := []string{".gitignore", "README.md", "docs/domain.md", "lib/main.go", "main/deep/nested/tool.sh", "main/run.sh", "notes/todo.md", "src/app/Main.tsx", "src/my_animation.ts"}
	if got := paths(t, res); !slices.Equal(got, want) || res["more"] != false {
		t.Fatalf("every file: %q more %v", got, res["more"])
	}
	if f := res["files"].([]any)[1].(map[string]any); f["size"] != float64(6) {
		t.Fatalf("README.md's size: %v", f["size"])
	}
	res = result(t, r.call("files.list", map[string]any{"agent": "alice", "query": "MaIn"}))
	if got := paths(t, res); !slices.Equal(got, []string{"lib/main.go", "docs/domain.md", "src/app/Main.tsx", "main/run.sh",
		"main/deep/nested/tool.sh", "src/my_animation.ts"}) {
		t.Fatalf("name, then path, then spread out: %q", got)
	}
	res = result(t, r.call("files.list", map[string]any{"agent": "alice", "query": "main", "limit": 2}))
	if got := paths(t, res); !slices.Equal(got, []string{"lib/main.go", "docs/domain.md"}) || res["more"] != true {
		t.Fatalf("limit 2: %q more %v", got, res["more"])
	}
	res = result(t, r.call("files.list", map[string]any{"agent": "alice", "query": "main", "limit": 6}))
	if res["more"] != false {
		t.Fatal("more with every match returned")
	}
}

func TestFilesListRefusesBadParamsAndAnAgentWithNoWorktree(t *testing.T) {
	r := gitRig(t)
	for _, c := range []struct {
		params map[string]any
		want   string
	}{
		{map[string]any{"agent": "alice", "limit": 0}, "bad:limit"},
		{map[string]any{"agent": "alice", "limit": 201}, "bad:limit"},
		{map[string]any{"agent": "alice", "limit": true}, "bad:limit"},
		{map[string]any{"agent": "alice", "limit": 1.5}, "bad:limit"},
		{map[string]any{"agent": "alice", "query": 7}, "bad:query"},
		{map[string]any{}, "bad:agent"},
		{map[string]any{"agent": "nobody"}, "not_found"},
	} {
		if got := refusal(r.call("files.list", c.params)); got != c.want {
			t.Errorf("%v: %s, want %s", c.params, got, c.want)
		}
	}
}

func TestFilesListTakesTheWholeLimitListsAConflictOnceAndFailsWithoutGit(t *testing.T) {
	r := gitRig(t)
	if res := result(t, r.call("files.list", map[string]any{"agent": "alice", "limit": 200})); len(res["files"].([]any)) != 9 {
		t.Fatalf("limit 200: %v", res)
	}
	// A file both sides changed: git lists it once per stage while the merge is open.
	r.git("checkout", "-q", "-b", "other")
	r.put("README.md", "theirs\n")
	r.git("commit", "-q", "-am", "theirs")
	r.git("checkout", "-q", "main")
	r.put("README.md", "ours\n")
	r.git("commit", "-q", "-am", "ours")
	if out, err := r.gitOut("merge", "other"); err == nil {
		t.Fatalf("the merge did not conflict: %s", out)
	}
	res := result(t, r.call("files.list", map[string]any{"agent": "alice", "query": "readme"}))
	if got := paths(t, res); !slices.Equal(got, []string{"README.md"}) {
		t.Fatalf("a conflicted file: %q", got)
	}

	plain := newRig(t) // a worktree that is no git repository
	if m := plain.call("files.list", map[string]any{"agent": "alice"}); m["error"] == nil {
		t.Fatalf("listed without git: %v", m)
	}
	os.RemoveAll(plain.wt)
	if got := refusal(plain.call("files.list", map[string]any{"agent": "alice"})); got != "not_found" {
		t.Fatalf("a worktree that is gone: %s", got)
	}
}
