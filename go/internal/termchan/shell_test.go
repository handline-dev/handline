package termchan

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/cmdruns"
	"github.com/handline/handline/go/internal/inputhold"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/term"
)

// shellHost is a host that opens shells: each is a new pane stamped with its agent, listed until closed.
type shellHost struct {
	*fakeHost
	panes  map[string]string // handle -> agent of a shell
	cwds   []string
	closed []string
	runs   []string
	ended  map[string]term.PaneRun // run windows tmux keeps after their command
}

func newShellHost(h *fakeHost) *shellHost { return &shellHost{fakeHost: h, panes: map[string]string{}} }

func (h *shellHost) ListTerminals() map[string]bool {
	m := map[string]bool{"%1": true}
	for p := range h.panes {
		m[p] = true
	}
	return m
}

func (h *shellHost) CreateShell(cwd, _, agent string) (string, string) {
	handle := "%" + string(rune('2'+len(h.cwds)))
	h.cwds = append(h.cwds, cwd)
	h.panes[handle] = agent
	return handle, ""
}

func (h *shellHost) PaneShell(handle string) (string, bool) { return h.panes[handle], true }

func (h *shellHost) CloseTerminal(handle string, _ bool) (bool, string) {
	delete(h.panes, handle)
	h.closed = append(h.closed, handle)
	return true, ""
}

func (h *shellHost) CreateTerminal(string, string, string, string) (string, string) { return "", "" }
func (h *shellHost) RespawnTerminal(string) (bool, string)                          { return false, "" }

func shellRig(t *testing.T) (rig, *shellHost, string) {
	r := newRig(t)
	r.agent(t)
	wt := t.TempDir()
	if _, err := r.e.DB.Exec("UPDATE agent_state SET worktree = ? WHERE agent = 'ann'", wt); err != nil {
		t.Fatal(err)
	}
	h := newShellHost(r.h)
	r.e.Backend = func() term.Backend { return h }
	return r, h, wt
}

func TestShellOpenStartsAShellInTheWorktreeAndEachCallAnother(t *testing.T) {
	r, h, wt := shellRig(t)
	a, err := r.e.shellOpen(person("casey"), map[string]any{"agent": "ann"})
	if err != nil {
		t.Fatal(err)
	}
	b, _ := r.e.shellOpen(person("casey"), map[string]any{"agent": "ann"})
	am, bm := a.(map[string]any), b.(map[string]any)
	if am["terminal"] == bm["terminal"] || am["terminal"] == "%1" || am["cwd"] != wt || len(h.cwds) != 2 || h.cwds[0] != wt {
		t.Fatalf("two shells, neither the agent's own terminal, in the worktree: %v %v %v", am, bm, h.cwds)
	}
}

func TestShellOpenRefusals(t *testing.T) {
	r, h, _ := shellRig(t)
	for _, p := range []map[string]any{{}, {"agent": 3}} {
		var re *rpc.Error
		if _, err := r.e.shellOpen(person("casey"), p); !errors.As(err, &re) || re.Data["field"] != "agent" {
			t.Fatalf("%v: %v", p, err)
		}
	}
	if _, err := r.e.shellOpen(person("casey"), map[string]any{"agent": "nobody.here"}); err == nil {
		t.Fatal("an agent nobody has")
	}
	r.e.DB.Exec("UPDATE agent_state SET worktree = '/no/such/dir' WHERE agent = 'ann'")
	if _, err := r.e.shellOpen(person("casey"), map[string]any{"agent": "ann"}); err == nil || len(h.cwds) != 0 {
		t.Fatalf("an agent whose worktree is gone: %v", err)
	}
	r.e.DB.Exec("UPDATE agent_state SET worktree = ? WHERE agent = 'ann'", t.TempDir())
	for i := 0; i < MaxShells; i++ {
		if _, err := r.e.shellOpen(person("casey"), map[string]any{"agent": "ann"}); err != nil {
			t.Fatal(i, err)
		}
	}
	if _, err := r.e.shellOpen(person("casey"), map[string]any{"agent": "ann"}); err == nil || !strings.Contains(err.Error(), "close one") {
		t.Fatalf("one over the most: %v", err)
	}
}

func TestAShellTakesKeystrokesAndResizesWithNoHolderButTheAgentsTerminalStillDoesNot(t *testing.T) {
	r, h, _ := shellRig(t)
	r.e.Holds = &inputhold.Store{DB: r.e.DB, Changed: r.e.HoldersChanged, Now: r.e.Now}
	got, _ := r.e.shellOpen(person("casey"), map[string]any{"agent": "ann"})
	sh := got.(map[string]any)["terminal"].(string)
	data := base64.StdEncoding.EncodeToString([]byte("ls\r"))
	if _, err := r.e.input(person("casey"), map[string]any{"terminal": sh, "data": data}); err != nil {
		t.Fatal(err)
	}
	if len(h.sent) != 1 || string(h.sent[0]) != "ls\r" {
		t.Fatalf("sent %q", h.sent)
	}
	if _, err := r.e.resize(person("casey"), map[string]any{"terminal": sh, "cols": json.Number("40"), "rows": json.Number("10")}); err != nil {
		t.Fatalf("a shell has no size floor: %v", err)
	}
	var re *rpc.Error
	if _, err := r.e.input(person("casey"), map[string]any{"terminal": "%1", "data": data}); !errors.As(err, &re) || re.Data["gate"] != "input_holder" {
		t.Fatalf("the agent's own terminal still wants the input held: %v", err)
	}
}

func TestShellCloseClosesAShellAndNothingElse(t *testing.T) {
	r, h, _ := shellRig(t)
	got, _ := r.e.shellOpen(person("casey"), map[string]any{"agent": "ann"})
	sh := got.(map[string]any)["terminal"].(string)
	if _, err := r.e.shellClose(person("casey"), map[string]any{"terminal": "%1"}); err == nil || len(h.closed) != 0 {
		t.Fatalf("the agent's own terminal is not a shell: %v %v", err, h.closed)
	}
	if _, err := r.e.shellClose(person("casey"), map[string]any{"terminal": sh}); err != nil || len(h.closed) != 1 || h.closed[0] != sh {
		t.Fatalf("%v %v", err, h.closed)
	}
	if _, err := r.e.shellClose(person("casey"), map[string]any{"terminal": sh}); err == nil {
		t.Fatal("a shell already closed")
	}
}

func (h *shellHost) CreateRun(cwd, title, agent, tag, command string) (string, string) {
	handle, why := h.CreateShell(cwd, title, agent)
	h.runs = append(h.runs, tag+" "+command)
	return handle, why
}

func (h *shellHost) PaneRuns() map[string]term.PaneRun { return h.ended }

func TestShellOpenWithACommandRunsItAndRecordsTheRunOfItsBlock(t *testing.T) {
	r, h, wt := shellRig(t)
	got, err := r.e.shellOpen(person("casey"), map[string]any{"agent": "ann", "command": "go test ./...", "block": "12:1"})
	if err != nil {
		t.Fatal(err)
	}
	m := got.(map[string]any)
	runs, _ := cmdruns.Of(r.e.DB, 12)
	if len(runs) != 1 || runs[0].Block != "12:1" || runs[0].Terminal != m["terminal"] || runs[0].State != "running" ||
		len(h.runs) != 1 || h.runs[0] != runs[0].Tag()+" go test ./..." || m["cwd"] != wt {
		t.Fatalf("%v %+v %q", m, runs, h.runs)
	}
}

func TestShellOpenCommandAndBlockGoTogetherAndAreChecked(t *testing.T) {
	r, h, _ := shellRig(t)
	for _, c := range []struct {
		p     map[string]any
		field string
	}{
		{map[string]any{"command": "true"}, "block"},
		{map[string]any{"block": "1:0"}, "command"},
		{map[string]any{"command": "", "block": "1:0"}, "command"},
		{map[string]any{"command": 5, "block": "1:0"}, "command"},
		{map[string]any{"command": strings.Repeat("x", maxCommand+1), "block": "1:0"}, "command"},
		{map[string]any{"command": "true", "block": "first"}, "block"},
		{map[string]any{"command": "true", "block": 7}, "block"},
	} {
		c.p["agent"] = "ann"
		var re *rpc.Error
		if _, err := r.e.shellOpen(person("casey"), c.p); !errors.As(err, &re) || re.Data["field"] != c.field || len(h.cwds) != 0 {
			t.Fatalf("%v: %v", c.p, err)
		}
	}
	if runs, _ := cmdruns.All(r.e.DB); len(runs) != 0 {
		t.Fatalf("a refused call records nothing: %+v", runs)
	}
}

func TestAFinishedRunsWindowIsNotAShellInUseAndARerunClosesItsOld(t *testing.T) {
	r, h, _ := shellRig(t)
	h.ended = map[string]term.PaneRun{}
	open := func(block string) string {
		got, err := r.e.shellOpen(person("casey"), map[string]any{"agent": "ann", "command": "true", "block": block})
		if err != nil {
			t.Fatal(block, err)
		}
		return got.(map[string]any)["terminal"].(string)
	}
	first := open("5:0")
	h.ended[first] = term.PaneRun{Tag: cmdruns.Run{Block: "5:0", Started: cmdruns.Now()}.Tag()}
	other := open("5:1")
	h.ended[other] = term.PaneRun{Tag: cmdruns.Run{Block: "5:1", Started: cmdruns.Now()}.Tag()}
	if again := open("5:0"); len(h.closed) != 1 || h.closed[0] != first || again == first || other == first {
		t.Fatalf("a rerun of a block closes the window it had: %v", h.closed)
	}
	shell, _ := r.e.shellOpen(person("casey"), map[string]any{"agent": "ann"})
	plain := shell.(map[string]any)["terminal"].(string)
	cmdruns.Start(r.e.DB, cmdruns.Block{Message: 7, N: 0}, plain, cmdruns.Now()) // after a tmux restart a run row names a handle a plain shell now has
	open("7:0")
	for _, c := range h.closed {
		if c == plain {
			t.Fatalf("a rerun closed the plain shell that has the old handle: %v", h.closed)
		}
	}
	for i := 0; i < MaxShells+2; i++ {
		w := open("6:" + strconv.Itoa(i))
		h.ended[w] = term.PaneRun{Dead: true}
	}
	if _, err := r.e.shellOpen(person("casey"), map[string]any{"agent": "ann"}); err != nil {
		t.Fatalf("kept windows of finished commands fill no cap: %v", err)
	}
}
