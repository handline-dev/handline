package adminwrites

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestAccountAddMakesAndLinksTheDirectoryAndLogsNothingIn(t *testing.T) {
	r := newRig(t)
	shared := filepath.Join(r.home, "shared", ".claude")
	os.MkdirAll(filepath.Join(shared, "skills"), 0o755)
	os.WriteFile(filepath.Join(shared, "settings.json"), []byte("{}"), 0o644)
	var told []string
	r.e.Changed = func(a string) { told = append(told, a) }
	call := `{"account":"spare","request_id":"ad1"}`
	res := r.result(r.one("account.add", call))
	if res["account"] != "spare" || res["accepted"] != true {
		t.Fatalf("%v", res)
	}
	cdir := filepath.Join(r.home, "accounts", "spare", ".claude")
	for item, want := range map[string]string{"settings.json": filepath.Join(shared, "settings.json"), "skills": filepath.Join(shared, "skills")} {
		if got, err := os.Readlink(filepath.Join(cdir, item)); err != nil || got != want {
			t.Errorf("%s -> %q (%v), want %q", item, got, err, want)
		}
	}
	if _, err := os.Stat(filepath.Join(cdir, ".credentials.json")); err == nil {
		t.Fatal("an account that was only added is logged in")
	}
	if len(told) != 1 || told[0] != "spare" {
		t.Fatalf("the accounts topic was told %v", told)
	}
	if again := r.result(r.one("account.add", call)); again["replayed"] != true || len(told) != 1 {
		t.Fatalf("a retry answers what the first did: %v", again)
	}
	// another vendor records itself; one with no driver is refused and leaves nothing
	r.result(r.one("account.add", `{"account":"oc","vendor":"opencode","request_id":"ad2"}`))
	if got := r.file("accounts", "oc", "vendor"); got != "opencode\n" {
		t.Fatalf("vendor file %q", got)
	}
	m := r.one("account.add", `{"account":"x","vendor":"nope","request_id":"ad3"}`)
	if r.code(m) != "bad_request" || m["error"].(map[string]any)["data"].(map[string]any)["field"] != "vendor" {
		t.Fatalf("%v", m)
	}
	if _, err := os.Stat(filepath.Join(r.home, "accounts", "x")); err == nil {
		t.Fatal("a refusal left an account behind")
	}
}

func TestAccountAddRefusesABadNameAndANameInUse(t *testing.T) {
	r := newRig(t)
	for i, name := range []string{"", "a/b", ".hidden", "yes: no"} {
		m := r.one("account.add", fmt.Sprintf(`{"account":%q,"request_id":"bn%d"}`, name, i))
		if r.code(m) != "bad_request" || m["error"].(map[string]any)["data"].(map[string]any)["field"] != "account" {
			t.Errorf("%q: %v", name, m)
		}
	}
	m := r.one("account.add", `{"account":"main","request_id":"bn9"}`)
	if r.code(m) != "refused" || m["error"].(map[string]any)["data"].(map[string]any)["gate"] != "state" {
		t.Fatalf("a name in use: %v", m)
	}
}

// backedUp makes a snapshot the gate takes as current.
func (r *rig) backedUp() {
	dir := filepath.Join(filepath.Dir(r.home), filepath.Base(r.home)+"-backups")
	os.MkdirAll(dir, 0o755)
	os.WriteFile(filepath.Join(dir, "orcapipe-20260101-000000.tar.gz"), nil, 0o644)
}

func (r *rig) loggedIn(acct string) {
	cdir := filepath.Join(r.home, "accounts", acct, ".claude")
	os.MkdirAll(cdir, 0o755)
	os.WriteFile(filepath.Join(cdir, ".credentials.json"), []byte("{}"), 0o600)
}

func TestAccountRemoveDryRunChangesAndRecordsNothing(t *testing.T) {
	r := newRig(t)
	r.loggedIn("main")
	res := r.result(r.one("account.remove", `{"account":"main","dry_run":true,"request_id":"dr1"}`))
	plan, _ := res["plan"].([]any)
	if res["would_remove"] != false || len(plan) < 5 || !strings.HasPrefix(plan[len(plan)-1].(string), "would refuse: no backup from the last 5 minutes") {
		t.Fatalf("%v", res)
	}
	r.backedUp()
	res = r.result(r.one("account.remove", `{"account":"main","dry_run":true,"request_id":"dr2"}`))
	plan, _ = res["plan"].([]any)
	if res["would_remove"] != true || plan[len(plan)-1] != "backup gate: passed" || !strings.Contains(plan[1].(string), "claude auth logout") {
		t.Fatalf("%v", res)
	}
	if _, err := os.Stat(filepath.Join(r.home, "accounts", "main")); err != nil {
		t.Fatal("a dry run moved the account")
	}
	var events, replies int
	r.e.DB.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'rpc_write'").Scan(&events)
	r.e.DB.QueryRow("SELECT COUNT(*) FROM rpc_replies").Scan(&replies)
	if events != 0 || replies != 0 {
		t.Fatalf("a dry run recorded %d events and %d replies", events, replies)
	}
}

func TestAccountRemoveLogsOutMovesTheDirectoryAsideAndForgetsItOnTheAgents(t *testing.T) {
	r := newRig(t)
	r.loggedIn("main")
	r.backedUp()
	for _, q := range []string{
		"INSERT INTO agent_state(agent, state, updated, account, account_source, resume_session, closed_at) VALUES('alice', 'idle', 1, 'main', 'x', 'sess', 5)",
		"INSERT INTO cursors(name, row_id, updated) VALUES('usage-poll:main', 3, 0)"} {
		if _, err := r.e.DB.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	var ran []string
	r.e.Run = func(argv []string, env map[string]string) int {
		ran = append(ran, strings.Join(argv, " "))
		os.Remove(filepath.Join(r.home, "accounts", "main", ".claude", ".credentials.json")) // claude removes its own credential
		return 0
	}
	if m := r.one("account.remove", `{"account":"main","request_id":"rm0"}`); r.code(m) != "bad_request" {
		t.Fatalf("without the confirmation: %v", m)
	}
	if m := r.one("account.remove", `{"account":"main","confirm":"other","request_id":"rm0"}`); r.code(m) != "bad_request" {
		t.Fatalf("with another name: %v", m)
	}
	res := r.result(r.one("account.remove", `{"account":"main","confirm":"main","request_id":"rm1"}`))
	if len(ran) != 1 || ran[0] != "claude auth logout" || res["accepted"] != true || !strings.HasPrefix(res["said"].(string), "removed account 'main' (logged out; directory kept at ") {
		t.Fatalf("%v %v", ran, res)
	}
	if _, err := os.Stat(filepath.Join(r.home, "accounts", "main")); err == nil {
		t.Fatal("the account is still on the roster")
	}
	kept, _ := filepath.Glob(filepath.Join(r.home, "accounts", ".retired", "main-*", ".claude"))
	if len(kept) == 0 {
		t.Fatal("the directory was not kept")
	}
	var account, resume any
	r.e.DB.QueryRow("SELECT account, resume_session FROM agent_state WHERE agent = 'alice'").Scan(&account, &resume)
	var polls int
	r.e.DB.QueryRow("SELECT COUNT(*) FROM cursors WHERE name = 'usage-poll:main'").Scan(&polls)
	if account != nil || resume != nil || polls != 0 {
		t.Fatalf("account %v resume %v polls %d", account, resume, polls)
	}
	if m := r.one("account.remove", `{"account":"main","confirm":"main","request_id":"rm2"}`); r.code(m) != "not_found" {
		t.Fatalf("a retry after the removal: %v", m)
	}
}

func TestAccountRemoveIsRefusedWhileAnAgentIsOnItWithoutABackupAndWhenTheLogoutFails(t *testing.T) {
	r := newRig(t)
	r.loggedIn("main")
	r.e.Run = func([]string, map[string]string) int { return 1 }
	rm := func(rid string) map[string]any {
		return r.one("account.remove", fmt.Sprintf(`{"account":"main","confirm":"main","request_id":%q}`, rid))
	}
	refusal := func(m map[string]any) string {
		t.Helper()
		if r.code(m) != "refused" || m["error"].(map[string]any)["data"].(map[string]any)["gate"] != "state" {
			t.Fatalf("not a state refusal: %v", m)
		}
		return m["error"].(map[string]any)["message"].(string)
	}
	if got := refusal(rm("g1")); !strings.Contains(got, "not removing 'main': no backup from the last 5 minutes") {
		t.Fatalf("%q", got)
	}
	r.backedUp()
	if _, err := r.e.DB.Exec("INSERT INTO agent_state(agent, state, updated, account) VALUES('alice', 'idle', 1, 'main')"); err != nil {
		t.Fatal(err)
	}
	if got := refusal(rm("g2")); !strings.Contains(got, "has a session open for alice") {
		t.Fatalf("%q", got)
	}
	r.e.DB.Exec("UPDATE agent_state SET closed_at = ? WHERE agent = 'alice'", time.Now().Unix())
	if got := refusal(rm("g3")); !strings.Contains(got, "logout failed - 'main' is still logged in, so it stays") {
		t.Fatalf("%q", got)
	}
	if _, err := os.Stat(filepath.Join(r.home, "accounts", "main", ".claude", ".credentials.json")); err != nil {
		t.Fatal("a refused removal touched the account")
	}
	if _, err := os.Stat(filepath.Join(r.home, "accounts", "main", ".removing")); err == nil {
		t.Fatal("the removal marker was left behind")
	}
}
