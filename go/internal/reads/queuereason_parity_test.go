package reads

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Python's queue_reason (actions/stuck.py), run on the database the Go one reads, with the world it asks Orca and the process table for
// taken as unknown, which is how it reads a structured agent: the terminal checks fall away.
const pythonQueueReason = `
import importlib, os, sqlite3, sys
sys.path.insert(0, os.getcwd())
stuck = importlib.import_module("handline.actions.stuck")  # handline.actions.stuck is also a function
stuck._world = lambda ttl=0: (None, None)
con = sqlite3.connect(os.path.join(os.environ["OCP_HOME"], "db.sqlite")); con.row_factory = sqlite3.Row
print(stuck.queue_reason(con, sys.argv[1], int(sys.argv[2])))
`

func pythonReason(t *testing.T, home, agent string, rid int64) string {
	t.Helper()
	root, _ := filepath.Abs("../../..")
	cmd := exec.Command("python3", "-I", "-c", pythonQueueReason, agent, fmt.Sprint(rid))
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "OCP_HOME="+home, "HANDLINE_HOME="+home, "HANDLINE_TEST_ROOT="+filepath.Dir(home), "OCP_TEST_ROOT="+filepath.Dir(home))
	out, err := cmd.CombinedOutput()
	if errors.Is(err, exec.ErrNotFound) {
		t.Skip("no python3:", err)
	}
	if err != nil { // Python ran and failed: the parity is broken, not unavailable
		t.Fatalf("the Python queue_reason failed: %v: %s", err, out)
	}
	return strings.TrimSpace(string(out))
}

// withTerminal: Python reads a missing terminal before anything else; a structured agent has none and Go does not look, so each state is
// given the handle a terminal agent would have.
func withTerminal(t *testing.T, e *Env) {
	run(t, e, "UPDATE agent_state SET terminal = 'h1' WHERE agent = 'bob'")
}

func TestQueueReasonIsThePythonQueueReasonInEachState(t *testing.T) {
	cases := []struct {
		name  string
		setup func(*Env)
		d     Delivery
		rid   int64
	}{
		{"not registered", func(e *Env) {}, nil, 1},
		{"never started", func(e *Env) { addAgent(t, e, "bob", "") }, nil, 1},
		{"stalled", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "STALLED", nil); withTerminal(t, e) }, nil, 1},
		{"a turn running", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "RUNNING", nil); withTerminal(t, e) }, nil, 1},
		{"mid-handoff", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "HANDOFF", nil); withTerminal(t, e) }, nil, 1},
		{"nothing holds it", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "IDLE", nil); withTerminal(t, e) }, nil, 1},
		{"closed, waking", func(e *Env) { addAgent(t, e, "bob", ""); state(t, e, "IDLE", 5); withTerminal(t, e) }, fakeDelivery{cause: "work"}, 1},
		{"closed, holding", func(e *Env) {
			addAgent(t, e, "bob", "")
			state(t, e, "IDLE", 5)
			withTerminal(t, e)
			run(t, e, "UPDATE requests SET delivered_at = 4 WHERE id = 1")
		}, fakeDelivery{}, 1},
		{"closed, nothing to wake it", func(e *Env) {
			addAgent(t, e, "bob", "")
			state(t, e, "IDLE", 5)
			withTerminal(t, e)
			run(t, e, "UPDATE requests SET state = 'answered' WHERE id = 1")
		}, fakeDelivery{}, 1},
		{"switched with its terminal open", func(e *Env) {
			addAgent(t, e, "bob", "")
			os.WriteFile(filepath.Join(e.Home, "agents", "bob", "agent.yaml"), []byte("structured: true\n"), 0o644)
			e.Structured = func(a string) bool { return a == "bob" }
			state(t, e, "IDLE", nil)
			withTerminal(t, e)
		}, nil, 1},
		{"behind the head", func(e *Env) {
			addAgent(t, e, "bob", "")
			state(t, e, "IDLE", nil)
			withTerminal(t, e)
			queue(t, e, 2, "task")
		}, nil, 2},
		{"a say with a turn running", func(e *Env) {
			addAgent(t, e, "bob", "")
			state(t, e, "RUNNING", nil)
			withTerminal(t, e)
			queue(t, e, 2, "say")
		}, nil, 2},
		{"a say with nothing in the way", func(e *Env) {
			addAgent(t, e, "bob", "")
			state(t, e, "IDLE", nil)
			withTerminal(t, e)
			queue(t, e, 2, "say")
			run(t, e, "UPDATE requests SET state = 'answered' WHERE id = 1")
		}, nil, 2},
	}
	for _, c := range cases {
		e := testEnv(t)
		queue(t, e, 1, "task")
		c.setup(e)
		e.Delivery = c.d
		got, err := e.QueueReason("bob", c.rid)
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		want := pythonReason(t, e.Home, "bob", c.rid)
		t.Logf("%s: %q", c.name, want)
		if got != want {
			t.Errorf("%s: Go says %q, Python %q", c.name, got, want)
		}
	}
}
