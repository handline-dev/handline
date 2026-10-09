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
