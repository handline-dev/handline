package compose

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/team"
)

// stallWorld is a database and a home whose account "acct" has a credential written an hour ago.
func stallWorld(t *testing.T) (*sql.DB, team.Accounts, string) {
	t.Helper()
	home := t.TempDir()
	dir := filepath.Join(home, "accounts", "acct", ".claude")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	cred := filepath.Join(dir, ".credentials.json")
	os.WriteFile(cred, []byte("c"), 0o600)
	old := time.Now().Add(-time.Hour)
	os.Chtimes(cred, old, old)
	conn, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	return conn, team.Accounts{Home: home}, cred
}

func addEvent(t *testing.T, d *sql.DB, ts int64, agent, kind, payload string) {
	t.Helper()
	if _, err := d.Exec("INSERT INTO events(ts, agent, account, kind, payload) VALUES(?, ?, 'acct', ?, ?)", ts, agent, kind, payload); err != nil {
		t.Fatal(err)
	}
}

func TestAStopOnAStalledAccountLiftsTheStall(t *testing.T) {
	d, accounts, _ := stallWorld(t)
	p := loginPorts(accounts, nil, nil)
	now := time.Now().Unix()
	if !p.Stall(d, "a", "acct", "oauth_org_not_allowed", "refused", now-10) {
		t.Fatal("not stalled")
	}
	if _, errType, ok := p.Stalled(d, "acct"); !ok || errType != "oauth_org_not_allowed" || accounts.MayStart("acct") {
		t.Fatalf("%v %v: no agent may be placed on a stalled account", errType, ok)
	}
	addEvent(t, d, now, "b", "Stop", `{}`)
	if _, _, ok := p.Stalled(d, "acct"); ok || accounts.Stalled("acct") != nil {
		t.Fatal("a Stop on the account since the stall lifts it")
	}
}

func TestOneCardPerAccountNotPerAgent(t *testing.T) {
	d, accounts, _ := stallWorld(t)
	p := loginPorts(accounts, nil, nil)
	now := time.Now().Unix()
	if !p.Stall(d, "a", "acct", "oauth_org_not_allowed", "refused", now) || p.Stall(d, "b", "acct", "oauth_org_not_allowed", "refused", now) {
		t.Fatal("the first sighting stands")
	}
	var n int
	d.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'login_expired'").Scan(&n)
	if n != 1 {
		t.Fatalf("%d login_expired events", n)
	}
}

func TestUnliftedFailuresOfOneErrorAreCountedOnTheAccount(t *testing.T) {
	d, accounts, _ := stallWorld(t)
	p := loginPorts(accounts, nil, nil)
	then := time.Now().Add(-10 * time.Minute).Unix()
	for i, agent := range []string{"a", "b", "a", "b"} {
		addEvent(t, d, then+int64(i), agent, "StopFailure", `{"error": "brand_new_error"}`)
	}
	addEvent(t, d, then+5, "c", "StopFailure", `{"error": "server_error"}`)
	if errType, _, _, ok := p.Failed(d, "b", "acct"); !ok || errType != "brand_new_error" || p.Repeats(d, "acct", "brand_new_error") != 4 {
		t.Fatalf("%v %v %d", errType, ok, p.Repeats(d, "acct", "brand_new_error"))
	}
}

func TestALoginSinceTheFailuresLiftsThemAndStopsTheirCount(t *testing.T) {
	d, accounts, cred := stallWorld(t)
	p := loginPorts(accounts, nil, nil)
	then := time.Now().Add(-10 * time.Minute).Unix()
	for i := int64(0); i < 4; i++ {
		addEvent(t, d, then+i, "a", "StopFailure", `{"error": "brand_new_error"}`)
	}
	os.Chtimes(cred, time.Now(), time.Now()) // a login writes it anew
	if _, _, _, ok := p.Failed(d, "a", "acct"); ok || p.Repeats(d, "acct", "brand_new_error") != 0 {
		t.Fatal("a failure before a login says nothing of the account now")
	}
}

func TestTheReconcilerReleasesAStalledAgentOnceAStopLiftsTheStall(t *testing.T) {
	d, accounts, _ := stallWorld(t)
	p := loginPorts(accounts, nil, nil)
	now := time.Now().Unix()
	addEvent(t, d, now-10, "a", "StopFailure", `{"error": "account_on_hold"}`)
	p.Stall(d, "a", "acct", "account_on_hold", "held", now-10)
	if err := db.SetAgentState(d, "a", states.Stalled, 0, "account_on_hold on acct and no other account can take it (none) - hl login acct"); err != nil {
		t.Fatal(err)
	}
	if _, err := d.Exec("UPDATE agent_state SET account = 'acct' WHERE agent = 'a'"); err != nil {
		t.Fatal(err)
	}
	lift := liftLogin(accounts)
	var actions []string
	row, _ := db.GetAgentState(d, "a")
	if lift(d, "a", row, &actions); len(actions) != 0 {
		t.Fatalf("lifted while stalled: %v", actions)
	}
	addEvent(t, d, now, "b", "Stop", `{}`)
	if lift(d, "a", row, &actions); len(actions) != 1 {
		t.Fatalf("%v", actions)
	}
	if row, _ = db.GetAgentState(d, "a"); row.State != states.Idle {
		t.Fatalf("%+v", row)
	}
}

type raised []string

func (r *raised) RaiseDecision(_ db.Q, agent, text, _ string) (int64, error) {
	*r = append(*r, agent+": "+text)
	return int64(len(*r)), nil
}

func TestTheFounderIsToldOnceAnAccountsAgentsHaveNowhereToGo(t *testing.T) {
	d, accounts, _ := stallWorld(t)
	var r raised
	p := loginPorts(accounts, nil, &r)
	ended := "subscription ended 2026-10-21 (month cycle)"
	// Every tick, for each of its agents, and with the reason no other account has room moving as their windows do.
	p.NoRoom(d, "a", "acct", ended, "acct's subscription has ended (other: 5h window at the wall)")
	p.NoRoom(d, "b", "acct", ended, "acct's subscription has ended (other: weekly window at the wall)")
	if len(r) != 1 || r[0] != "a: No account can take acct's agents\n\nacct's subscription has ended (other: 5h window at the wall)" {
		t.Fatalf("%q", r)
	}
	p.NoRoom(d, "a", "acct", "a's turn ended on billing_error", "acct cannot be used")
	p.NoRoom(d, "a", "other", ended, "other's subscription has ended")
	if len(r) != 3 {
		t.Fatalf("a new stall or another account is news: %q", r)
	}
}
