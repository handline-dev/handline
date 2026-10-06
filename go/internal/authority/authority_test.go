package authority

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/peer"
)

func write(t *testing.T, root, ns, name, body string) {
	t.Helper()
	os.MkdirAll(filepath.Join(root, ns), 0o700)
	if err := os.WriteFile(filepath.Join(root, ns, name), []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

// procs: 300 is the agent's recorded session (started at "5.000001"), 400 a child of it, 500 a daemonized
// orphan whose parent is launchd, 600 a process that took the session's pid after it ended.
var procs = map[int]peer.Proc{
	300: {PPid: 2, Start: "5.000001"},
	400: {PPid: 300, Start: "9.000000"},
	500: {PPid: 1, Start: "9.000000"},
	600: {PPid: 2, Start: "7.000000"},
}

func store(t *testing.T) *Store {
	root := t.TempDir()
	write(t, root, sessionNS, "agent-a", "300 5.000001")
	write(t, root, sessionNS, "agent-b", "600 5.000001") // recorded start differs from the live process
	write(t, root, founderNS, "key", "s3cret\n")
	return &Store{Root: root, Lookup: func(pid int) (peer.Proc, bool) { p, ok := procs[pid]; return p, ok }}
}

func TestAgentIsKnownOnlyByDescent(t *testing.T) {
	s := store(t)
	for pid, want := range map[int]string{300: "agent-a", 400: "agent-a", 500: "", 600: "", 0: "", 999: ""} {
		if got := s.AgentOf(pid); got != want {
			t.Errorf("pid %d: %q, want %q", pid, got, want)
		}
	}
}

func TestFounderKey(t *testing.T) {
	s := store(t)
	if !s.FounderKeyOK("s3cret") || s.FounderKeyOK("s3cre") || s.FounderKeyOK("") {
		t.Fatal("the key is compared whole")
	}
	if (&Store{Root: t.TempDir()}).FounderKeyOK("") {
		t.Fatal("no key kept is no founder")
	}
}

func TestUnreadableStoreGrantsNothing(t *testing.T) {
	s := &Store{Root: filepath.Join(t.TempDir(), "nope"), Lookup: func(int) (peer.Proc, bool) { return peer.Proc{}, false }}
	if s.AgentOf(300) != "" || s.FounderKeyOK("x") {
		t.Fatal("granted")
	}
}

func TestLiveProcessLookup(t *testing.T) {
	p, ok := peer.Lookup(os.Getpid())
	if !ok || p.PPid != os.Getppid() || p.Start == "" {
		t.Fatalf("%+v %v", p, ok)
	}
}
