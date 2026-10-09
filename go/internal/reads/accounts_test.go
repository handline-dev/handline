package reads

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/weblogin"
)

func TestTheAccountsViewNeverCarriesTheAuthorizeURL(t *testing.T) {
	e := testEnv(t)
	if err := os.MkdirAll(filepath.Join(e.Home, "accounts", "main"), 0o755); err != nil {
		t.Fatal(err)
	}
	for _, st := range []string{weblogin.Waiting, "code_sent", weblogin.Done, weblogin.Failed, weblogin.Cancelled} {
		e.Login = func(string) *weblogin.Login {
			return &weblogin.Login{ID: "login_1", State: st, URL: "https://claude.example/authorize?s=secret"}
		}
		acct, ok := e.Account("main")
		if !ok {
			t.Fatal("no account")
		}
		l, _ := acct["login"].(map[string]any)
		if l["state"] != st || l["login_id"] != "login_1" {
			t.Fatalf("%s: %v", st, l)
		}
		list, err := e.accountsList(nil, nil)
		if err != nil {
			t.Fatal(err)
		}
		raw, _ := json.Marshal([]any{acct, list})
		if strings.Contains(string(raw), "claude.example") || strings.Contains(string(raw), `"url"`) {
			t.Fatalf("%s: the accounts view has the URL: %s", st, raw)
		}
	}
}

func TestAccountsListUsageIsOptInAndSaysWhyAnAccountCannotStart(t *testing.T) {
	e := testEnv(t)
	dir := filepath.Join(e.Home, "accounts", "main")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(dir, "subscription.json"), []byte(`{"subscriptionType":"max"}`), 0o644)
	plain, _ := e.accountsList(nil, nil)
	if _, has := plain.(map[string]any)["accounts"].([]any)[0].(map[string]any)["assignable"]; has {
		t.Fatal("the usage fields are only there when asked for")
	}
	got, err := e.accountsList(nil, map[string]any{"usage": true})
	if err != nil {
		t.Fatal(err)
	}
	a := got.(map[string]any)["accounts"].([]any)[0].(map[string]any)
	if a["plan"] != "max" || a["assignable"] != false || !strings.Contains(a["assign_reason"].(string), "no status.json") || a["runway"] != "" || a["ctx_pct"] != nil {
		t.Fatalf("%v", a)
	}
	if _, err := e.accountsList(nil, map[string]any{"usage": "yes"}); err == nil {
		t.Fatal("a usage that is not a boolean is refused")
	}
}

func TestStatusPickNamesNoAccountThatIsNotLoggedIn(t *testing.T) {
	e := testEnv(t)
	if err := os.MkdirAll(filepath.Join(e.Home, "accounts", "main"), 0o755); err != nil {
		t.Fatal(err)
	}
	got, err := e.statusPick(nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	m := got.(map[string]any)
	if m["picked"] != nil || len(m["candidates"].([]any)) != 0 || len(m["agent_costs"].([]string)) != 0 {
		t.Fatalf("%v", m)
	}
}
