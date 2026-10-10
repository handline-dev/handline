package termchan

import (
	"errors"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/term"
)

// attachingHost is a host with a command to attach by and a window to bring to the front.
type attachingHost struct {
	*fakeHost
	focused []string
}

func (a *attachingHost) AttachCommand() []string { return []string{"tmux", "-L", "x", "attach-session", "-t", "ocp"} }
func (a *attachingHost) Focus(handle string) bool {
	a.focused = append(a.focused, handle)
	return true
}

func TestOpenSaysWhereTheTerminalIsAndHowToAttachAndTypesNothing(t *testing.T) {
	r := newRig(t)
	r.agent(t)
	got, err := r.e.open(person("casey"), map[string]any{"agent": "ann"})
	m, _ := got.(map[string]any)
	if err != nil || m["agent"] != "ann" || m["terminal"] != "%1" || m["host"] != "fake" || m["attach"] != nil || m["focused"] != false || m["local"] != true {
		t.Fatalf("a host with no command to attach by: %v %v", got, err)
	}
	h := &attachingHost{fakeHost: r.h}
	r.e.Backend = func() term.Backend { return h }
	got, err = r.e.open(person("casey"), map[string]any{"agent": "ann"})
	m, _ = got.(map[string]any)
	argv, _ := m["attach"].(map[string]any)["argv"].([]string)
	if err != nil || len(argv) != 6 || argv[3] != "attach-session" || m["focused"] != true || len(h.focused) != 1 || h.focused[0] != "%1" {
		t.Fatalf("%v %v", got, err)
	}
	if len(r.h.sent) != 0 {
		t.Fatal("open typed into the terminal")
	}
	remote := device("phone")
	remote.Remote = "100.64.0.2"
	if got, _ := r.e.open(remote, map[string]any{"agent": "ann"}); got.(map[string]any)["local"] != false {
		t.Fatalf("a caller over the network is not on the daemon's machine: %v", got)
	}
}

func TestOpenRefusesWhatIsNotAnAgentWithATerminal(t *testing.T) {
	r := newRig(t)
	r.agent(t)
	for _, p := range []map[string]any{{}, {"agent": 3}, {"agent": ""}} {
		var re *rpc.Error
		if _, err := r.e.open(person("casey"), p); !errors.As(err, &re) || re.Data["field"] != "agent" {
			t.Fatalf("%v: %v", p, err)
		}
	}
	if _, err := r.e.open(person("casey"), map[string]any{"agent": "nobody.here"}); err == nil {
		t.Fatal("an agent nobody registered")
	}
	if _, err := r.e.DB.Exec("UPDATE agent_state SET terminal = NULL WHERE agent = 'ann'"); err != nil {
		t.Fatal(err)
	}
	if _, err := r.e.open(person("casey"), map[string]any{"agent": "ann"}); err == nil {
		t.Fatal("an agent with no terminal")
	}
}
