package compose

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/usage"
)

// atPct writes acct's status at pct of its 5h window, as a session's statusline leaves it.
func atPct(t *testing.T, home, acct string, pct int) {
	t.Helper()
	now := time.Now().Unix()
	win := func(minutes, pct int) map[string]any {
		return map[string]any{"minutes": minutes, "used_percent": pct, "resets_at": now + 3600, "observed_at": now, "carried": false, "lost": false}
	}
	b, _ := json.Marshal(map[string]any{"collected_at": now, "windows": []any{win(300, pct), win(10080, 10)}})
	if err := os.WriteFile(filepath.Join(paths.AccountDir(home, acct), "status.json"), b, 0o644); err != nil {
		t.Fatal(err)
	}
}

// A closed agent's wait on its own account, read through the daemon's launcher: AccountWait's word, and the cause a task waiting on the
// agent is filed under (Task.waiting.on).
func TestAWaitOnAClosedAgentIsFiledUnderCapWhenItsAccountIsFullAndWallAtItsLimit(t *testing.T) {
	p, lc, env, _ := wired(t)
	d, home := p.db, p.home
	d.Exec("UPDATE requests SET state = 'closed'") // wired's begin, ahead of the task
	dbpkg.SetClosed(d, "alice", "s-alice")
	dbpkg.SetAccount(d, "alice", "a1", "")
	now := time.Now().Unix()
	dbpkg.BlockOpen(d, "alice", "dialog", "a prompt", "", now, now, "", "") // the task waits on alice itself
	tid, err := dbpkg.NewTask(d, "fix it", "alice", "human", 0, dbpkg.DefaultPriority, "", 0, "")
	if err != nil {
		t.Fatal(err)
	}
	dbpkg.SetTaskQueued(d, tid, "human")
	rid, _ := dbpkg.AddRequest(d, dbpkg.NewRequest{Requester: "human", Target: "alice", Body: "fix it", Kind: "task"})
	dbpkg.LinkRequest(d, tid, rid)
	check := func(wait, on string) {
		t.Helper()
		if w, err := (lifecycleStarter{m: lc.Manager}).AccountWait(d, "alice"); err != nil || w != wait {
			t.Fatalf("AccountWait %q %v, want %q", w, err, wait)
		}
		if err := env.SweepHolds(time.Now().Unix()); err != nil {
			t.Fatal(err)
		}
		raw, _, _ := dbpkg.Meta(d, fmt.Sprintf("taskhold:%d", tid))
		var rec struct{ On string }
		if json.Unmarshal([]byte(raw), &rec); rec.On != on {
			t.Fatalf("filed under %q, want %q: %s", rec.On, on, raw)
		}
	}
	atPct(t, home, "a1", 10)
	check("", "owner")
	for i := range usage.DefaultActiveCap {
		other := fmt.Sprint("busy", i)
		dbpkg.SetAgentState(d, other, states.Running, 0, "")
		dbpkg.SetAccount(d, other, "a1", "")
	}
	check("cap", "cap") // the others mid-turn on a1 fill its cap
	atPct(t, home, "a1", 100)
	check("wall", "wall")
}
