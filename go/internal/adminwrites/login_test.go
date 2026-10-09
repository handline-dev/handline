package adminwrites

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

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
