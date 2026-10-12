package compose

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
)

func TestOnlyASessionThatBypassesPromptsIsAttestedToAndTheWeakestAuthorCounts(t *testing.T) {
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	for _, k := range []string{"OCP_AGENT", "HANDLINE_AGENT"} {
		t.Setenv(k, "")
	}
	home := filepath.Join(t.TempDir(), "home")
	if err := os.MkdirAll(home, 0o755); err != nil { // the protected store is kept under the home as it resolves
		t.Fatal(err)
	}
	r := team.NewRoster(home)
	bypass := "bypass"
	for name, mode := range map[string]*string{"free": &bypass, "free2": &bypass, "asks": nil} {
		if _, err := r.Register(name, team.RegisterOpts{Mode: mode}); err != nil {
			t.Fatal(err)
		}
	}
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	ask := func(from string) string {
		rid, err := db.AddRequest(d, db.NewRequest{Requester: from, Target: "free", Body: "x", Response: "async", Kind: "ask"})
		if err != nil {
			t.Fatal(err)
		}
		return supervisor.RequestOp(rid)
	}
	// free asked "asks", who answered, and free asked "free2", who answered; "asks" asked "free" and "free" is told it was cancelled
	asked := func(from, to string) int64 {
		rid, err := db.AddRequest(d, db.NewRequest{Requester: from, Target: to, Body: "x", Response: "async", Kind: "ask"})
		if err != nil {
			t.Fatal(err)
		}
		return rid
	}
	byPrompting, byBypass, cancelled := asked("free", "asks"), asked("free", "free2"), asked("asks", "free")
	// the task board tells "free" what "asks" wrote as T9's evidence: its words under the board's name
	board, err := db.AddRequest(d, db.NewRequest{Requester: db.NoticeRequester, Target: "free", Body: "T9 done by asks: fine", Response: "fire_and_forget", Kind: "say"})
	if err != nil {
		t.Fatal(err)
	}
	from := peerFrom(home, d)
	mode := func(agent, op string) string { return from(agent, op).Mode }
	for _, c := range []struct{ agent, op, want string }{
		{"free", ask("human"), peerinbox.Bypass},                              // the person's words carry the authority the session was launched with
		{"free", ask("free2"), peerinbox.Bypass},                              // a bypassing agent's
		{"free", ask("asks"), peerinbox.Prompting},                            // a prompting agent's are not passed off as the person's
		{"free", fmt.Sprintf("answers:%d", byPrompting), peerinbox.Prompting}, // an answer is its writer's words
		{"free", fmt.Sprintf("answers:%d", byBypass), peerinbox.Bypass},
		{"free", fmt.Sprintf("answers:%d,%d", byBypass, byPrompting), peerinbox.Prompting},
		{"free", fmt.Sprintf("cancelled:%d", cancelled), peerinbox.Prompting},
		{"free", supervisor.RequestOp(board), ""}, // a task-board notice quotes an agent: not known whose words
		{"free", "pending:1", ""},                 // an operation not known to carry the person's, the daemon's or an agent's words attests nothing
		{"free", "answers:999", ""},               // nor one whose requests cannot be read
		{"asks", ask("free2"), ""},                // a session that prompts takes the message as it always did
	} {
		if got := mode(c.agent, c.op); got != c.want {
			t.Errorf("%s %s: %q, want %q", c.agent, c.op, got, c.want)
		}
	}
}

func TestADeliveryNamesWhoWroteItAndNeverTheSessionsOwnPerson(t *testing.T) {
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	for _, k := range []string{"OCP_AGENT", "HANDLINE_AGENT"} {
		t.Setenv(k, "")
	}
	home := filepath.Join(t.TempDir(), "home")
	if err := os.MkdirAll(home, 0o755); err != nil {
		t.Fatal(err)
	}
	r := team.NewRoster(home)
	for _, name := range []string{"free", "asks", "other"} {
		if _, err := r.Register(name, team.RegisterOpts{}); err != nil {
			t.Fatal(err)
		}
	}
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	op := func(from string) string {
		rid, err := db.AddRequest(d, db.NewRequest{Requester: from, Target: "free", Body: "x", Response: "async", Kind: "ask"})
		if err != nil {
			t.Fatal(err)
		}
		return supervisor.RequestOp(rid)
	}
	from := peerFrom(home, d)
	for _, c := range []struct{ op, name string }{
		{op("asks"), "asks"},
		{op("human"), "founder"},
		{supervisor.DaemonOp, ""},
		{"notice:1", ""},
	} {
		if got := from("free", c.op); got.Name != c.name || got.Mode != "" { // "free" prompts: no class is attested to it
			t.Errorf("%s: %+v, want name %q", c.op, got, c.name)
		}
	}
}
