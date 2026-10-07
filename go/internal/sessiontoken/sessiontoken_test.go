package sessiontoken

import (
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/peer"
	"github.com/handline/handline/go/internal/protected"
)

func P(ppid int, start string) peer.Proc { return peer.Proc{PPid: ppid, Start: start} }

// procs: 300 is a session (started "5.000001"), 400 a child of it, 500 an orphan, 600 a process that took 300's pid later.
func tokens(t *testing.T, procs map[int]peer.Proc) *Tokens {
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	t.Setenv("OCP_AGENT", "")
	t.Setenv("HANDLINE_AGENT", "")
	return &Tokens{Store: protected.New(filepath.Join(t.TempDir(), "home")),
		Lookup: func(pid int) (peer.Proc, bool) { p, ok := procs[pid]; return p, ok }}
}

func TestRecordThenVerifyByDescent(t *testing.T) {
	tk := tokens(t, map[int]peer.Proc{300: P(2, "5.000001"), 400: P(300, "9.0"), 500: P(1, "9.0")})
	if !tk.Record("a", 300) {
		t.Fatal("not recorded")
	}
	for pid, want := range map[int]bool{300: true, 400: true, 500: false, 999: false, 0: false} {
		if tk.Verify("a", pid) != want {
			t.Errorf("pid %d: want %v", pid, want)
		}
	}
	if tk.AgentOf(400) != "a" || tk.AgentOf(500) != "" {
		t.Fatal("AgentOf")
	}
	tk.Lookup = func(pid int) (peer.Proc, bool) { return map[int]peer.Proc{300: P(2, "7.0")}[pid], pid == 300 }
	if tk.Verify("a", 300) {
		t.Fatal("a reused pid has another start time")
	}
}

func TestAnAgentSessionRecordsNothing(t *testing.T) {
	tk := tokens(t, map[int]peer.Proc{300: P(2, "5.0")})
	t.Setenv("OCP_AGENT", "worker")
	t.Setenv("HANDLINE_AGENT", "worker")
	if tk.Record("a", 300) || tk.MintKey("a") || tk.EnsureFounderKey() {
		t.Fatal("a session wrote to the founder's store")
	}
}

func TestKeys(t *testing.T) {
	tk := tokens(t, nil)
	if tk.KeyOK("a", "") || tk.FounderKeyOK("x") {
		t.Fatal("no key kept is no proof")
	}
	if !tk.MintKey("a") || !tk.EnsureFounderKey() {
		t.Fatal("not minted")
	}
	k, f := tk.KeyHeld("a"), tk.FounderKeyHeld()
	if len(k) < 40 || len(f) < 40 || k == f {
		t.Fatalf("%q %q", k, f)
	}
	if !tk.KeyOK("a", k) || tk.KeyOK("b", k) || tk.KeyOK("a", k[:len(k)-1]) || !tk.FounderKeyOK(f) || tk.FounderKeyOK(k) {
		t.Fatal("keys are compared whole and by owner")
	}
	if !tk.EnsureFounderKey() || tk.FounderKeyHeld() != f {
		t.Fatal("an existing founder key is kept")
	}
}

func TestIsClaude(t *testing.T) {
	for p, want := range map[string]bool{"/usr/local/bin/claude": true, "/opt/tools/share/claude/versions/2.1/claude-bin": true,
		"/usr/bin/node": false, "": false, "/x/@anthropic-ai/claude-code/cli.js": true} {
		if IsClaude(p) != want {
			t.Errorf("%q", p)
		}
	}
}

func TestChainAndNesting(t *testing.T) {
	tk := tokens(t, map[int]peer.Proc{10: P(20, "1"), 20: P(30, "1"), 30: P(1, "1")})
	tk.Lookup = func(pid int) (peer.Proc, bool) {
		return map[int]peer.Proc{10: P(20, "1"), 20: P(30, "1"), 30: P(1, "1")}[pid], pid == 10 || pid == 20 || pid == 30
	}
	if got := tk.Chain(10); len(got) != 2 || got[0].PID != 20 || got[1].PID != 30 {
		t.Fatalf("%v", got)
	}
	if tk.Nested(10) {
		t.Fatal("nothing is claude here")
	}
}
