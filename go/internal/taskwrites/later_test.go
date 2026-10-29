package taskwrites

import (
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestTheFounderDefersAnItemAndReturnsIt(t *testing.T) {
	sdb, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer sdb.Close()
	for i := 1; i <= 3; i++ {
		if _, err := sdb.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(?, 't', 'human', 'boss', 1)", i); err != nil {
			t.Fatal(err)
		}
	}
	sdb.Exec("UPDATE tasks SET done_evidence = 'x' WHERE id = 3")
	if _, err := sdb.Exec("INSERT INTO task_memos(id, task_id, author, body, created_at) VALUES(7, 1, 'w', '@human which?', 1)"); err != nil {
		t.Fatal(err)
	}
	if _, err := sdb.Exec("INSERT INTO requests(id, ts, requester, target, body, state, memo_id) VALUES(70, 1, 'w', 'human', '@human which?', 'queued', 7)"); err != nil {
		t.Fatal(err)
	}
	mk := func(agent string) *rpc.Server {
		s := rpc.NewServer(auth{agent}, "x", store.LatestVersion(), "local", func(string, ...any) {})
		s.DB = sdb
		Register(s, t.TempDir(), &fakeTeller{}, nil)
		return s
	}
	n := 0
	send := func(agent, method, params string) map[string]any {
		n++
		return call(t, mk(agent), fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":%q,"params":{%s,"request_id":"r%d"}}`, method, params, n))[0]
	}
	soon, past := time.Now().Add(time.Hour).UTC().Format(time.RFC3339), time.Now().Add(-time.Hour).UTC().Format(time.RFC3339)
	code := func(r map[string]any) string {
		e, _ := r["error"].(map[string]any)
		d, _ := e["data"].(map[string]any)
		c, _ := d["code"].(string)
		return c
	}
	held := func(kind, id string) *db.Later {
		l, err := db.LaterOf(sdb, kind, id, time.Now().Unix())
		if err != nil {
			t.Fatal(err)
		}
		return l
	}

	for _, ok := range []string{
		`"item":{"kind":"task","id":"1"},"until":"` + soon + `"`,
		`"item":{"kind":"task","id":2},"after_task":1`, // an id may be a number
		`"item":{"kind":"comment","id":"7"},"after_task":2`,
	} {
		if r := send("", "later.set", ok); r["error"] != nil {
			t.Fatalf("%s: %v", ok, r)
		}
	}
	if l := held("task", "1"); l == nil || !l.Until.Valid {
		t.Fatalf("task 1: %+v", l)
	}
	if l := held("comment", "7"); l == nil || l.After.Int64 != 2 {
		t.Fatalf("comment 7: %+v", l)
	}
	if r := send("", "later.set", `"item":{"kind":"task","id":"1"},"after_task":2`); r["error"] != nil {
		t.Fatalf("replace: %v", r)
	} else if l := held("task", "1"); l == nil || l.Until.Valid || l.After.Int64 != 2 {
		t.Fatalf("a second set replaces the first: %+v", l)
	}

	for want, bad := range map[string][]string{
		"refused": {
			`"item":{"kind":"task","id":"1"},"until":"` + past + `"`,    // not in the future
			`"item":{"kind":"task","id":"1"},"after_task":3`,            // already done
			`"item":{"kind":"task","id":"1"},"after_task":1`,            // its own task
			`"item":{"kind":"comment","id":"7"},"after_task":1`,         // the comment's own task
			`"item":{"kind":"task","id":"3"},"until":"` + soon + `"`,    // the item itself is closed
			`"item":{"kind":"session","id":"x"},"until":"` + soon + `"`, // an agent is stopped on it
			`"item":{"kind":"hr","id":"1"},"until":"` + soon + `"`,      // an agent is stopped on it
		},
		"not_found": {
			`"item":{"kind":"task","id":"99"},"until":"` + soon + `"`,
			`"item":{"kind":"task","id":"1"},"after_task":99`,
			`"item":{"kind":"comment","id":"99"},"until":"` + soon + `"`,
			`"item":{"kind":"machine","id":"disk"},"until":"` + soon + `"`, // no disk need now
		},
		"bad_request": {
			`"item":{"kind":"bogus","id":"1"},"until":"` + soon + `"`,
			`"item":{"kind":"task"},"until":"` + soon + `"`,
			`"item":{"kind":"task","id":"1"}`,
			`"item":{"kind":"task","id":"1"},"until":"` + soon + `","after_task":2`,
			`"item":{"kind":"task","id":"1"},"until":"tomorrow"`,
			`"item":{"kind":"task","id":"1"},"after_task":"two"`,
		},
	} {
		for _, params := range bad {
			if got := code(send("", "later.set", params)); got != want {
				t.Errorf("%s: %q, want %s", params, got, want)
			}
		}
	}
	if l := held("task", "1"); l == nil || l.After.Int64 != 2 {
		t.Errorf("a refused call changed the deferral: %+v", l)
	}

	db.SetCursor(sdb, "disk_full_since", 1_700_000_000)
	if r := send("", "later.set", `"item":{"kind":"machine","id":"disk"},"until":"`+soon+`"`); r["error"] != nil {
		t.Errorf("the disk need: %v", r)
	}
	if r := send("w", "later.set", `"item":{"kind":"task","id":"2"},"until":"`+soon+`"`); code(r) != "refused" {
		t.Errorf("an agent defers nothing: %v", r)
	}
	if r := send("w", "later.clear", `"item":{"kind":"task","id":"1"}`); code(r) != "refused" || held("task", "1") == nil {
		t.Errorf("an agent returns nothing: %v", r)
	}

	if r := send("", "later.clear", `"item":{"kind":"task","id":"1"}`); r["error"] != nil || held("task", "1") != nil {
		t.Errorf("clear: %v", r)
	}
	if got := code(send("", "later.clear", `"item":{"kind":"task","id":"1"}`)); got != "not_found" {
		t.Errorf("a second clear: %q", got)
	}
}
