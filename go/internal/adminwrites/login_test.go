package adminwrites

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/weblogin"
)

const loginURL = "https://claude.example/oauth/authorize?state=abc"

func loginRig(t *testing.T) *rig {
	t.Helper()
	r := newRig(t)
	bin := filepath.Join(t.TempDir(), "claude")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\necho "+loginURL+"\ncat > /dev/null\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	r.e.Logins = &weblogin.Manager{Home: r.home, Lookup: func(string) (string, error) { return bin, nil }}
	t.Cleanup(r.e.Logins.Close)
	Register(r.srv, r.e)
	return r
}

func TestAccountLoginAnswersTheURLAndKeepsItOutOfTheRecord(t *testing.T) {
	r := loginRig(t)
	if r.code(r.one("account.login", `{"account":"nobody","request_id":"l0"}`)) != "not_found" {
		t.Fatal("no such account")
	}
	res := r.result(r.one("account.login", `{"account":"main","request_id":"l1"}`))
	id, _ := res["login_id"].(string)
	if res["url"] != loginURL || res["state"] != "waiting" || id == "" {
		t.Fatalf("%v", res)
	}
	var kept string
	if err := r.e.DB.QueryRow("SELECT result_json FROM write_effects WHERE request_id = 'l1'").Scan(&kept); err != nil || strings.Contains(kept, "claude.example") {
		t.Fatalf("the request record keeps the URL: %q (%v)", kept, err)
	}
	again := r.result(r.one("account.login", `{"account":"main","request_id":"l1"}`))
	if again["replayed"] != true || again["login_id"] != id || again["url"] != loginURL {
		t.Fatalf("a retry gets the same login and, while it waits, its URL: %v", again)
	}
	if r.code(r.one("account.login", `{"account":"main","request_id":"l2"}`)) != "refused" {
		t.Fatal("a second login on the account is refused")
	}
}

func TestAccountLoginCancelIsIdempotentAndKnowsItsIds(t *testing.T) {
	r := loginRig(t)
	id := r.result(r.one("account.login", `{"account":"main","request_id":"c1"}`))["login_id"].(string)
	if r.code(r.one("account.login_cancel", `{"login_id":"login_nope","request_id":"c2"}`)) != "not_found" {
		t.Fatal("an id never issued is not found")
	}
	if r.code(r.one("account.login_cancel", `{"request_id":"c3"}`)) != "bad_request" {
		t.Fatal("login_id is required")
	}
	for _, rid := range []string{"c4", "c5"} {
		if r.result(r.one("account.login_cancel", `{"login_id":"`+id+`","request_id":"`+rid+`"}`))["accepted"] != true {
			t.Fatal("cancel is accepted, twice")
		}
	}
	if l := r.e.Logins.Of("main"); l == nil || l.State != weblogin.Cancelled {
		t.Fatalf("%+v", l)
	}
	if _, ok := r.result(r.one("account.login", `{"account":"main","request_id":"c6"}`))["url"]; !ok {
		t.Fatal("a new login can start after the cancel")
	}
}

const plantedCode = "PLANTED-CODE-7f3a91"

// everything is every cell of every table of the database as text, so a planted string can be looked for anywhere.
func everything(t *testing.T, db *sql.DB) string {
	t.Helper()
	var all strings.Builder
	tabs, err := db.Query("SELECT name FROM sqlite_master WHERE type = 'table'")
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for tabs.Next() {
		var n string
		tabs.Scan(&n)
		names = append(names, n)
	}
	tabs.Close()
	for _, n := range names {
		rows, err := db.Query("SELECT * FROM \"" + n + "\"")
		if err != nil {
			t.Fatal(err)
		}
		cols, _ := rows.Columns()
		for rows.Next() {
			cells := make([]any, len(cols))
			ptrs := make([]any, len(cols))
			for i := range cells {
				ptrs[i] = &cells[i]
			}
			rows.Scan(ptrs...)
			fmt.Fprintln(&all, n, cells)
			for _, c := range cells {
				if b, ok := c.([]byte); ok {
					all.Write(b)
				}
			}
		}
		rows.Close()
	}
	return all.String()
}

func TestAccountLoginCodeIsWriteOnly(t *testing.T) {
	r := loginRig(t)
	var logged strings.Builder
	s := rpc.NewServer(auth{}, "x", store.LatestVersion(), "local", func(f string, a ...any) { fmt.Fprintf(&logged, f+"\n", a...) })
	s.DB = r.e.DB
	Register(s, r.e)
	r.srv = s
	id := r.result(r.one("account.login", `{"account":"main","request_id":"k1"}`))["login_id"].(string)
	if r.code(r.one("account.login_code", `{"login_id":"login_nope","code":"`+plantedCode+`","request_id":"k2"}`)) != "not_found" {
		t.Fatal("an id never issued is not found")
	}
	if r.code(r.one("account.login_code", `{"login_id":"`+id+`","request_id":"k3"}`)) != "bad_request" {
		t.Fatal("code is required")
	}
	res := r.result(r.one("account.login_code", `{"login_id":"`+id+`","code":"`+plantedCode+`","request_id":"k4"}`))
	if res["accepted"] != true || strings.Contains(fmt.Sprint(res), plantedCode) {
		t.Fatalf("the reply echoes the code: %v", res)
	}
	if l, _ := r.e.Logins.ByID(id); l.State != weblogin.CodeSent {
		t.Fatalf("%+v", l)
	}
	// a second code, with a new request id and with the same one, is refused each time; the refusal does not carry the code
	for _, rid := range []string{"k5", "k4"} {
		m := r.one("account.login_code", `{"login_id":"`+id+`","code":"`+plantedCode+`2","request_id":"`+rid+`"}`)
		if r.code(m) != "refused" || strings.Contains(fmt.Sprint(m), "PLANTED") {
			t.Fatalf("%v", m)
		}
	}
	if db := everything(t, r.e.DB); strings.Contains(db, "PLANTED") {
		t.Fatal("the code is in the database")
	}
	if strings.Contains(logged.String(), "PLANTED") {
		t.Fatalf("the code is in the log:\n%s", logged.String())
	}
}
