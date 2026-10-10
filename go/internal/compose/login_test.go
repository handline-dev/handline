package compose

import (
	"strings"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/tick"
)

func TestAnAuthFailedTurnMarksTheAccountAndStallsTheAgentAndALoginLiftsIt(t *testing.T) {
	p, _, _, _ := wired(t)
	d := p.Sink.DB
	acc := team.Accounts{Home: p.home}
	tk := p.Tick(p.Run.cfg, TickConfig{})
	if _, err := tk.Tick.Run(nil); err != nil { // the first tick wakes her: the check is for an agent with a session open
		t.Fatal(err)
	}
	if !p.Run.WaitIdle("alice", 10*time.Second) {
		t.Fatal("her turn ends")
	}
	if err := dbpkg.SetAccount(d, "alice", "a1", "recorded"); err != nil {
		t.Fatal(err)
	}
	if _, err := dbpkg.AddEventFor(d, "alice", "a1", "StopFailure", dbpkg.KV{K: "error", V: "authentication_failed"}, dbpkg.KV{K: "error_details", V: "401"}); err != nil {
		t.Fatal(err)
	}
	res, err := tk.Tick.Run(nil)
	if err != nil {
		t.Fatal(err)
	}
	if acc.LoginExpired("a1") == nil {
		t.Logf("%v", res.Actions)
		t.Fatal("the refused login did not mark the account")
	}
	row, _ := dbpkg.GetAgentState(d, "alice")
	if row.State != "STALLED" || !strings.Contains(row.Detail.String, tick.LoginStall) {
		t.Fatalf("no other account can take it, so it stalls for a person: %+v", row)
	}
	var n int
	d.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'login_expired' AND account = 'a1'").Scan(&n)
	if n != 1 {
		t.Fatalf("one login_expired event, got %d", n)
	}
	// Still marked: the lift leaves the stall, and a second tick does not mark again.
	var actions []string
	lift := liftLogin(acc)
	lift(d, "alice", row, &actions)
	if len(actions) != 0 {
		t.Fatalf("lifted while the account is still marked: %v", actions)
	}
	// The login is renewed: the stall ends, and the failed turn that is still the last event cannot mark the account again.
	acc.ClearLoginExpired("a1")
	lift(d, "alice", row, &actions)
	row, _ = dbpkg.GetAgentState(d, "alice")
	if row.State != "IDLE" || len(actions) != 1 {
		t.Fatalf("not lifted: %+v %v", row, actions)
	}
	if _, err := tk.Tick.Run(nil); err != nil {
		t.Fatal(err)
	}
	if acc.LoginExpired("a1") != nil {
		t.Fatal("the lifted failure marked the account a second time")
	}
}
