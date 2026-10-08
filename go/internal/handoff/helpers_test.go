package handoff

import (
	"database/sql"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/store"
)

type fakeHeads struct{ heads map[string]*db.Request }

func (f fakeHeads) EffectiveHead(_ db.Q, agent string) (*db.Request, error) {
	return f.heads[agent], nil
}

type fakeTelling struct {
	busy   map[string]bool
	typed  []string
	accept bool
}

func (f *fakeTelling) HasBackgroundWork(_ db.Q, agent string) (bool, error) {
	return f.busy[agent], nil
}
func (f *fakeTelling) TypeNotice(_ db.Q, agent, text string) (bool, error) {
	if f.accept {
		f.typed = append(f.typed, agent+": "+text)
	}
	return f.accept, nil
}

type fakeSettings struct {
	global map[string]bool
	agent  map[string]bool // agent+"/"+key
}

func (f *fakeSettings) Flag(k string, def bool) bool {
	if v, ok := f.global[k]; ok {
		return v
	}
	return def
}
func (f *fakeSettings) SetFlag(k string, on bool) error { f.global[k] = on; return nil }
func (f *fakeSettings) AgentFlag(agent, k string) (bool, bool) {
	v, ok := f.agent[agent+"/"+k]
	return v, ok
}

type world struct {
	*Env
	t        *testing.T
	heads    fakeHeads
	telling  *fakeTelling
	settings *fakeSettings
	now      int64
	logs     []string
	status   map[string][2]string
}

func newWorld(t *testing.T) *world {
	t.Helper()
	home := t.TempDir()
	conn, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	w := &world{t: t, now: 1_800_000_000, heads: fakeHeads{map[string]*db.Request{}}, telling: &fakeTelling{busy: map[string]bool{}},
		settings: &fakeSettings{global: map[string]bool{}, agent: map[string]bool{}}, status: map[string][2]string{}}
	db.Now = func() int64 { return w.now }
	t.Cleanup(func() { db.Now = func() int64 { return time.Now().Unix() } })
	w.Env = &Env{DB: conn, Home: home, Heads: w.heads,
		AgentSession: func(agent, account string) (string, string) { s := w.status[agent]; return s[0], s[1] },
		Now:          func() time.Time { return time.Unix(w.now, 0) },
		Note:         NoteEnv{Settings: w.settings, Telling: w.telling, Log: func(f string, a ...any) { w.logs = append(w.logs, fmt.Sprintf(f, a...)) }}}
	return w
}

func (w *world) exec(q string, args ...any) {
	w.t.Helper()
	if _, err := w.DB.Exec(q, args...); err != nil {
		w.t.Fatal(err)
	}
}

func (w *world) agent(name, state, account, worktree string) {
	w.t.Helper()
	w.exec("INSERT INTO agent_state(agent, state, updated, account, worktree) VALUES(?,?,?,?,?)", name, state, w.now, nullable(account), nullable(worktree))
}

func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func (w *world) event(agent, kind, payload string) int64 {
	w.t.Helper()
	r, err := w.DB.Exec("INSERT INTO events(ts, agent, kind, payload) VALUES(?,?,?,?)", w.now, agent, kind, nullable(payload))
	if err != nil {
		w.t.Fatal(err)
	}
	id, _ := r.LastInsertId()
	return id
}

func told(id int64, requester, body string) *db.Request {
	return &db.Request{ID: id, Requester: requester, Body: body, DeliveredAt: sql.NullInt64{Int64: 5, Valid: true}}
}

// gitRepo makes a repository with one commit and returns its path.
func gitRepo(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	for _, args := range [][]string{{"init", "-q", "-b", "work"}, {"-c", "user.email=git@example.com", "-c", "user.name=n", "commit", "-q", "--allow-empty", "-m", "first"}} {
		cmd := exec.Command("git", args...)
		cmd.Dir = dir
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v %s", args, err, out)
		}
	}
	return dir
}

func (w *world) transcript(account, project, session string) string {
	w.t.Helper()
	dir := filepath.Join(w.Home, "accounts", account, ".claude", "projects", project)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		w.t.Fatal(err)
	}
	p := filepath.Join(dir, session+".jsonl")
	if err := os.WriteFile(p, []byte("{}\n"), 0o644); err != nil {
		w.t.Fatal(err)
	}
	return p
}

func timeFormat(unix int64) string {
	return time.Unix(unix, 0).UTC().Format("2006-01-02T15:04:05.000Z")
}
