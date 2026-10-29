package reads

import (
	"fmt"
	"strconv"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
)

// seedForYou makes one open item of every kind, and returns their (kind, id) keys.
func seedForYou(t *testing.T, e *Env) []string {
	t.Helper()
	now := time.Now().Unix()
	for _, q := range []string{
		// task 1: given to the founder (owned by human, queued); task 2: tagged review, with a comment ask on it; task 3: an agent's own
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(1, 'pick one', 'human', 'boss', 10, 'boss')",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(2, 'look at it', 'w', 'boss', 20, 'boss')",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(3, 'draft', 'human', 'boss', 25)",
		"INSERT INTO task_tags(task_id, tag) VALUES(2, 'review')",
		"INSERT INTO task_memos(id, task_id, author, body, created_at) VALUES(7, 2, 'w', '@human is it right?', 30)",
		"INSERT INTO requests(id, ref, ts, requester, target, body, state, memo_id, edge_ok) VALUES(70, 'aaaaaaaaaaa7', 30, 'w', 'human', '@human is it right?', 'queued', 7, 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(2, 70)",
		// an ask on no task, and a say (nothing asked)
		"INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok) VALUES(71, 'aaaaaaaaaaa8', 40, 'w', 'human', 'which colour?', 'queued', 'ask', 1)",
		"INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok, landed_at) VALUES(72, 'aaaaaaaaaaa9', 41, 'w', 'human', 'fyi', 'landed', 'say', 1, 41)",
		"INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES('bob', 'permission', 'fp1', 't', '[]', '{\"tool\":\"Bash\",\"command\":\"ls\"}', " +
			strconv.FormatInt(now-100, 10) + ", " + strconv.FormatInt(now, 10) + ")",
	} {
		run(t, e, q)
	}
	putProposal(t, e, "0000000b", proposalFile("0000000b", "proposed", 50, 0, ""))
	db.SetCursor(e.DB, "disk_full_since", 60)
	db.SetCursor(e.DB, "disk_full_free", 3<<30)
	return []string{"task\x001", "comment\x007", "comment\x00aaaaaaaaaaa8", "prompt\x00fp1", "hr\x0011", "machine\x00disk"}
}

func forYou(t *testing.T, e *Env) (map[string]map[string]any, int) {
	t.Helper()
	items, count, err := e.ForYou()
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]map[string]any{}
	for _, it := range items {
		out[it["kind"].(string)+"\x00"+it["id"].(string)] = it
	}
	return out, count
}

func TestForYouListsEveryKindOfItemAndCountsThem(t *testing.T) {
	e := testEnv(t)
	keys := seedForYou(t, e)
	got, count := forYou(t, e)
	if len(got) != len(keys) || count != len(keys) {
		t.Fatalf("%d items, count %d, want %d: %v", len(got), count, len(keys), got)
	}
	for key, asks := range map[string]string{"task\x001": "decide", "comment\x007": "review", "comment\x00aaaaaaaaaaa8": "decide",
		"prompt\x00fp1": "allow", "hr\x0011": "allow", "machine\x00disk": "do"} {
		it := got[key]
		if it == nil || it["asks"] != asks || it["title"] == "" || it["since"] == nil {
			t.Errorf("%q: %v", key, it)
		}
	}
	if got["prompt\x00fp1"]["agent"] != "bob" || got["prompt\x00fp1"]["title"] != "ls" || got["comment\x007"]["agent"] != "w" || got["machine\x00disk"]["agent"] != nil {
		t.Errorf("who asks, and the title: %v", got)
	}
	if _, ok := got["task\x003"]; ok {
		t.Errorf("a draft is not asked of the founder")
	}
	items, _, _ := e.ForYou()
	for i := 1; i < len(items); i++ {
		if a, b := items[i-1]["since"].(string), items[i]["since"].(string); a > b {
			t.Errorf("oldest first: %s after %s", a, b)
		}
	}
}

func TestEachKindLeavesTheCountWhenItIsAnswered(t *testing.T) {
	e := testEnv(t)
	keys := seedForYou(t, e)
	for i, q := range []string{
		"UPDATE tasks SET done_evidence = 'x', done_at = 70 WHERE id = 1",
		"UPDATE requests SET state = 'landed' WHERE id = 70",
		"UPDATE requests SET state = 'landed' WHERE id = 71",
		"UPDATE prompt_blocks SET gone_at = 1 WHERE fingerprint = 'fp1'",
		"",
		"DELETE FROM cursors WHERE name = 'disk_full_since'",
	} {
		if q != "" {
			run(t, e, q)
		} else {
			putProposal(t, e, "0000000b", proposalFile("0000000b", "rejected", 50, 80, ""))
		}
		got, count := forYou(t, e)
		if want := len(keys) - i - 1; count != want || got[keys[i]] != nil {
			t.Errorf("after closing %q: count %d, want %d, still %v", keys[i], count, want, got[keys[i]])
		}
	}
}

// deferNav keeps the founder's Later pref, given as the JSON of nav.later.
func deferNav(t *testing.T, e *Env, later string) {
	t.Helper()
	run(t, e, `INSERT INTO prefs(person, nav, updated_at) VALUES('human', '{"later":`+later+`}', 0) ON CONFLICT(person) DO UPDATE SET nav = excluded.nav`)
}

func stamp(sec int64) string { return time.Unix(sec, 0).UTC().Format(time.RFC3339) }

func TestADeferredItemIsListedWithItsLaterAndLeftOutOfTheCount(t *testing.T) {
	e := testEnv(t)
	seedForYou(t, e)
	now := time.Now().Unix()
	run(t, e, "UPDATE tasks SET queued_by = 'boss' WHERE id = 3")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by) VALUES(4, 'waited on', 'w', 'boss', 5, 'boss')")
	deferNav(t, e, `{"task:1":{"until":"`+stamp(now+3600)+`"},"comment:7":{"after_task":4},"machine:disk":{"until":"`+stamp(now+60)+`"}}`)
	got, count := forYou(t, e)
	if count != 4 || len(got) != 7 {
		t.Fatalf("count %d of %d: %v", count, len(got), got)
	}
	for key, want := range map[string]string{
		"task\x001":               "map[until:" + stamp(now+3600) + "]",
		"comment\x007":            "map[after_task:4]",
		"machine\x00disk":         "map[until:" + stamp(now+60) + "]",
		"comment\x00aaaaaaaaaaa8": "map[]",
	} {
		l, _ := got[key]["later"].(map[string]any)
		if fmt.Sprint(l) != want {
			t.Errorf("%q: later %v, want %v", key, l, want)
		}
	}
}

func TestADeferralThatIsOverLeavesItsItemInTheCount(t *testing.T) {
	e := testEnv(t)
	seedForYou(t, e)
	now := time.Now().Unix()
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at, queued_by, done_evidence) VALUES(4, 'finished', 'w', 'boss', 5, 'boss', 'ok')")
	deferNav(t, e, `{"task:1":{"until":"`+stamp(now-1)+`"},"comment:7":{"after_task":4},"machine:disk":{"after_task":99}}`)
	got, count := forYou(t, e)
	if count != 6 || got["task\x001"]["later"] != nil || got["comment\x007"]["later"] != nil || got["machine\x00disk"]["later"] != nil {
		t.Fatalf("count %d: a time past, a task done, a task that is none: %v", count, got)
	}
}
