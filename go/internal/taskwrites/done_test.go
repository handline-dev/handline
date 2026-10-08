package taskwrites

import (
	"fmt"
	"os"
	osexec "os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestDoneIsTheOwnersWithOneKindOfEvidenceAndTellsWhoWaited(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	home := t.TempDir()
	for _, a := range []string{"w", "boss"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), nil, 0o644)
	}
	repo := t.TempDir()
	git := func(args ...string) string {
		t.Helper()
		cmd := osexec.Command("git", append([]string{"-c", "user.name=t", "-c", "user.email=t@example.com"}, args...)...)
		cmd.Dir = repo
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("git %v: %s", args, out)
		}
		return strings.TrimSpace(string(out))
	}
	git("init", "-q", "-b", "main")
	git("commit", "-q", "--allow-empty", "-m", "on main")
	onMain := git("rev-parse", "HEAD")
	git("checkout", "-q", "-b", "side")
	git("commit", "-q", "--allow-empty", "-m", "off main")
	offMain := git("rev-parse", "HEAD")
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, expects) VALUES(1, 'a', 'w', 'boss', 1, 'report'), (2, 'parent', 'w', 'boss', 1, 'report'), (3, 'kid', 'w', 'boss', 1, 'report'), " +
			"(4, 'waits', 'w', 'boss', 1, 'report'), (5, 'blocker', 'boss', 'boss', 1, 'report'), (6, 'sha', 'w', 'boss', 1, 'sha'), (7, 'ans', 'w', 'boss', 1, 'report'), (8, 'left', 'w', 'boss', 1, 'report')",
		"UPDATE tasks SET parent_task = 2 WHERE id = 3",
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(4, 5, 1)",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body) VALUES(10, 'boss', 'w', 'task', 'queued', 1, 'x'), (11, 'boss', 'w', 'say', 'landed', 1, 'x'), (12, 'boss', 'w', 'task', 'queued', 1, 'x')",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10), (8, 12)",
		"UPDATE tasks SET done_evidence = 'earlier', done_at = 2 WHERE id = 8",
	} {
		if _, err := db.Exec(q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	f := &fakeTeller{}
	mk := func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = db
		Register(s, home, f)
		return s
	}
	done := func(agent, rid string, task int, params string) map[string]any {
		return call(t, mk(agent), fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"task.done","params":{"task":%d,"request_id":%q%s}}`, task, rid, params))[0]
	}
	refused := func(why string, r map[string]any, want string) {
		t.Helper()
		if got := errText(r); got != want {
			t.Fatalf("%s: %q, want %q", why, got, want)
		}
	}
	refused("unknown", done("w", "a1", 99, `,"evidence":"x"`), fmt.Sprintf("no task T%d", 99))
	refused("not the owner", done("else", "a2", 1, `,"evidence":"x"`), "task T1 can only be marked done by its owner (w) or the founder")
	refused("both", done("w", "a3", 1, `,"evidence":"x","request":11`), "done takes exactly one of evidence or request")
	refused("neither", done("w", "a4", 1, ``), "done takes exactly one of evidence or request")
	refused("a queued request", done("w", "a5", 1, `,"request":10`), "request #10 is still queued - not answered yet")
	refused("a missing request", done("w", "a6", 1, `,"request":77`), "no request #77")
	refused("an open child", done("w", "a7", 2, `,"evidence":"x"`), "task T2 still has open child tasks (T3) - it is done once they are")
	refused("an open blocker", done("w", "a8", 4, `,"evidence":"x"`), "task T4 is waiting on T5 - it is done only once they are")
	refused("a sha off main", done("w", "a9", 1, fmt.Sprintf(`,"evidence":%q,"repo":%q`, offMain, repo)), offMain+" is not an ancestor of main")
	refused("a sha needs a repo", done("w", "b1", 1, `,"evidence":"abcd1234"`), "a sha is checked against a repo: name one")
	refused("a sha task closed by words", done("w", "b2", 6, `,"evidence":"it never merged"`), "task T6 expects a sha - `hl task done T6 <sha>`, or its opener (boss) or the founder may close it another way")

	r := done("w", "ok", 1, `,"evidence":"  all\n  done  "`)
	if r["error"] != nil || r["result"].(map[string]any)["said"] != "task T1 done (  all\n  done  )" {
		t.Fatalf("done: %v", r)
	}
	var via string
	db.QueryRow("SELECT done_via FROM tasks WHERE id = 1").Scan(&via)
	count := func(q string) (n int) { db.QueryRow(q).Scan(&n); return }
	if via != "reason" || count("SELECT COUNT(*) FROM requests WHERE id = 10 AND state = 'withdrawn'") != 1 {
		t.Fatalf("the evidence kind is kept and the leftover delivery is closed quietly: %q", via)
	}
	if count("SELECT COUNT(*) FROM requests WHERE kind = 'say' AND body = 'T1 done by w: all done' AND target = 'boss' AND notice IS NULL") != 1 ||
		count("SELECT COUNT(*) FROM events WHERE kind = 'mark_task_done'") != 1 || len(f.done) != 1 || f.done[0] != "T1 w w 1 true" {
		t.Fatalf("the opener is told once, typed, and the teller follows: %v", f.done)
	}

	r = done("boss", "c2", 6, `,"evidence":"it never merged"`)
	if r["error"] != nil {
		t.Fatalf("the opener closes a sha task another way: %v", r)
	}
	r = done("w", "c3", 7, fmt.Sprintf(`,"evidence":%q,"repo":%q`, onMain, repo))
	if r["error"] != nil || r["result"].(map[string]any)["said"] != "task T7 done ("+onMain+")" {
		t.Fatalf("a sha on main is stored in full: %v", r)
	}
	r = done("w", "c4", 8, `,"evidence":"again"`)
	if r["error"] != nil || r["result"].(map[string]any)["said"] != "task T8 is already done (earlier); its leftover request is settled" || f.done[len(f.done)-1] != "T8 w w 1 false" {
		t.Fatalf("a leftover request is settled: %v %v", r, f.done)
	}
}
