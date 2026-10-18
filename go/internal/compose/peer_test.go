package compose

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/delivery"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/team"
)

func TestATerminalAgentIsDeliveredThroughThePeerInboxUnlessItsYAMLSaysOff(t *testing.T) {
	home := t.TempDir()
	for agent, yaml := range map[string]string{"term": "peer_inbox: true # inbox\n", "plain": "mode: auto\n", "off": "peer_inbox: false\n", "strm": "structured: true\npeer_inbox: true\n"} {
		dir := filepath.Join(paths.AgentsDir(home), agent)
		os.MkdirAll(dir, 0o755)
		os.WriteFile(filepath.Join(dir, "agent.yaml"), []byte(yaml), 0o644)
	}
	p := New(Config{Home: home})
	port := p.agentsPort()
	if !port.Structured("term") || !port.Structured("plain") || port.Structured("off") || !port.Structured("strm") {
		t.Fatal("term, plain (no flag) and strm are delivered by a send path; off opted out")
	}
	if !PeerFromYAML(home)("no-such-agent") {
		t.Fatal("an agent with no agent.yaml is on the peer inbox too")
	}
	// term goes to the peer port: it has no record to find, so nothing is written; strm goes to the (empty) structured manager
	if _, err := port.Send("term", "op", "hi", delivery.Now); err == nil {
		t.Fatal("a peer agent with no session")
	}
	if port.Live("term") || port.Live("strm") {
		t.Fatal("live")
	}
}

// peerHome is a home with account "acct", and agent "term" recorded in worktree with a live session record in that directory.
func peerHome(t *testing.T, worktree, cwd string) (home string, d *sql.DB) {
	t.Helper()
	home = t.TempDir()
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	config, err := team.AccountConfigDir(home, "acct")
	if err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(config, "sessions")
	os.MkdirAll(dir, 0o755)
	pid := os.Getpid() // alive
	rec := fmt.Sprintf(`{"pid":%d,"sessionId":"s1","cwd":%q,"version":"2.1.296","peerProtocol":1,"messagingSocketPath":"/tmp/x.sock","status":"idle","updatedAt":1}`, pid, cwd)
	os.WriteFile(filepath.Join(dir, fmt.Sprintf("%d.json", pid)), []byte(rec), 0o644)
	os.WriteFile(filepath.Join(dir, fmt.Sprintf("%d.k.key", pid)), []byte("secret\n"), 0o600)
	if _, err := d.Exec("INSERT INTO agent_state(agent, state, updated, worktree, account, terminal) VALUES('term', 'running', 1, ?, 'acct', '%7')", worktree); err != nil {
		t.Fatal(err)
	}
	return home, d
}

func TestASessionIsFoundForAWorktreeRecordedAsRepoAndPath(t *testing.T) {
	work := t.TempDir()
	for _, recorded := range []string{work, "/some/repo::" + work} {
		home, d := peerHome(t, recorded, work)
		s, err := findSession(home, d, "term", noPane)
		if err != nil || s.Cwd != work {
			t.Fatalf("%q: %v %v", recorded, s, err)
		}
	}
	home, d := peerHome(t, "/some/repo::/gone/away", work)
	if _, err := findSession(home, d, "term", noPane); err == nil {
		t.Fatal("a worktree that is gone has no session")
	}
}

func noPane(string) int { return 0 }

// Agents that share a worktree and an account each find only the session in their own pane, and a closed one finds none.
func TestASessionInTheSameWorktreeIsAnotherAgentsUnlessItRunsInThisAgentsPane(t *testing.T) {
	work := t.TempDir()
	home, d := peerHome(t, work, work)
	own := func(handle string) int {
		if handle == "%7" {
			return os.Getpid() // the session record's process
		}
		return 0
	}
	if s, err := findSession(home, d, "term", own); err != nil || s.PID != os.Getpid() {
		t.Fatalf("its own pane: %v %v", s, err)
	}
	other := func(string) int { return 1 << 30 } // a pane whose process the session does not run under
	if _, err := findSession(home, d, "term", other); err == nil {
		t.Fatal("a session outside the agent's pane is another agent's")
	}
	if _, err := d.Exec("UPDATE agent_state SET terminal = NULL WHERE agent = 'term'"); err != nil {
		t.Fatal(err)
	}
	if _, err := findSession(home, d, "term", own); err == nil {
		t.Fatal("an agent with no terminal has no session")
	}
}
