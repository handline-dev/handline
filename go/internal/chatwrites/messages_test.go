package chatwrites

import (
	"fmt"
	"testing"

	dbpkg "github.com/handline/handline/go/internal/db"
)

func (r *rig) errData(m map[string]any) map[string]any {
	r.t.Helper()
	e, ok := m["error"].(map[string]any)
	if !ok {
		r.t.Fatalf("not an error: %v", m)
	}
	return e["data"].(map[string]any)
}

func TestSayWritesOnceAndRefusesWhatIsNotASay(t *testing.T) {
	r := newRig(t)
	s := r.server("alice")
	say := `{"target":"bob","body":"fyi","request_id":"m1"}`
	first, again := r.result(r.one(s, "say", say)), r.result(r.one(s, "say", say))
	if first["accepted"] != true || again["replayed"] != true || r.count("SELECT COUNT(*) FROM requests WHERE requester = 'alice' AND target = 'bob' AND kind = 'say'") != 1 {
		t.Fatalf("one say, a retry repeats nothing: %v %v", first, again)
	}
	if d := r.errData(r.one(s, "say", `{"target":"bob","body":"x","no_task":"why","request_id":"m2"}`)); d["code"] != "refused" {
		t.Fatalf("--no-task is for replying to the founder: %v", d)
	}
	if d := r.errData(r.one(s, "say", `{"target":"bob","body":" ","request_id":"m3"}`)); d["field"] != "body" {
		t.Fatal(d)
	}
}

func TestInboxMarkReadReadsAReportButNeverADecision(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	ask := r.request("alice", "human", "may I?")
	report, err := dbpkg.AddRequest(r.db, dbpkg.NewRequest{Requester: "alice", Target: "human", Body: "done", Kind: "say"})
	if err != nil {
		t.Fatal(err)
	}
	if d := r.errData(r.one(s, "inbox.mark_read", fmt.Sprintf(`{"ids":[%d],"request_id":"i1"}`, ask))); d["gate"] != "state" {
		t.Fatalf("a reply owed is answered, never read away: %v", d)
	}
	if d := r.errData(r.one(s, "inbox.mark_read", fmt.Sprintf(`{"ids":[%d],"request_id":"i2"}`, report))); d["gate"] != "state" {
		t.Fatalf("nothing behind a reply owed is read away: %v", d)
	}
	r.result(r.one(s, "inbox.answer", fmt.Sprintf(`{"id":%d,"text":"yes","request_id":"i4"}`, ask)))
	res := r.result(r.one(s, "inbox.mark_read", fmt.Sprintf(`{"ids":[%d],"request_id":"i5"}`, report)))
	if res["said"] != "1 read" || r.count("SELECT COUNT(*) FROM requests WHERE id = ? AND read_at IS NOT NULL", report) != 1 {
		t.Fatalf("%v", res)
	}
	if d := r.errData(r.one(s, "inbox.mark_read", `{"ids":[999999],"request_id":"i3"}`)); d["code"] != "not_found" {
		t.Fatal(d)
	}
}

func TestInboxAnswerAndReply(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	ask := r.request("alice", "human", "may I?")
	if d := r.errData(r.one(s, "inbox.answer", fmt.Sprintf(`{"id":%d,"option":"a","request_id":"n1"}`, ask))); d["field"] != "option" {
		t.Fatal(d)
	}
	if res := r.result(r.one(s, "inbox.answer", fmt.Sprintf(`{"id":%d,"text":"yes","request_id":"n2"}`, ask))); res["accepted"] != true {
		t.Fatal(res)
	}
	if r.count("SELECT COUNT(*) FROM requests WHERE settles = ? AND body = 'yes'", ask) != 1 {
		t.Fatal("answered")
	}
	// an agent replies to its own incoming item
	mine := r.request("bob", "alice", "question")
	if res := r.result(r.one(r.server("alice"), "inbox.reply", fmt.Sprintf(`{"id":%d,"text":"answer","request_id":"n3"}`, mine))); res["accepted"] != true {
		t.Fatal(res)
	}
	if r.count("SELECT COUNT(*) FROM requests WHERE settles = ? AND requester = 'alice'", mine) != 1 {
		t.Fatal("the reply settles the item")
	}
	if d := r.errData(r.one(r.server("alice"), "inbox.reply", fmt.Sprintf(`{"id":%d,"text":"x","request_id":"n4"}`, r.request("bob", "carol", "not yours")))); d["code"] != "not_found" {
		t.Fatal(d)
	}
}

func TestStandingAnswersAreAddedAndRemovedOnceAndARemovedOneIsNotFound(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	folder := t.TempDir()
	add := `{"folder":"` + folder + `","request_id":"r1"}`
	if res := r.result(r.one(s, "answer.add", add)); res["accepted"] != true {
		t.Fatal(res)
	}
	if res := r.result(r.one(s, "answer.add", add)); res["replayed"] != true {
		t.Fatalf("a retry repeats nothing: %v", res)
	}
	rm := `{"kind":"trust","key":["` + folder + `"],"request_id":"r3"}`
	if res := r.result(r.one(s, "answer.remove", rm)); res["accepted"] != true {
		t.Fatal(res)
	}
	if d := r.errData(r.one(s, "answer.remove", `{"kind":"trust","key":["`+folder+`"],"request_id":"r4"}`)); d["code"] != "not_found" {
		t.Fatalf("%v", d)
	}
	if r.count("SELECT COUNT(*) FROM cursors WHERE name LIKE 'intent:%'") != 0 {
		t.Fatal("no intent is left behind")
	}
}

// This daemon answers no dialog: a dialog rule is not saved, none is listed, and removing one finds nothing.
func TestADialogRuleIsRefusedAsUnsupportedAndNoneIsKept(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	d := r.errData(r.one(s, "answer.add", `{"vendor":"claude","title":"Trust this folder?","label":"Yes","request_id":"r1"}`))
	if d["code"] != "refused" || d["gate"] != "unsupported" {
		t.Fatalf("%v", d)
	}
	if d := r.errData(r.one(s, "answer.remove", `{"kind":"dialog","key":["claude","Trust this folder?","Yes"],"request_id":"r2"}`)); d["code"] != "not_found" {
		t.Fatalf("%v", d)
	}
	if r.count("SELECT COUNT(*) FROM cursors WHERE name LIKE 'intent:%'") != 0 {
		t.Fatal("a refusal leaves no intent behind")
	}
}

func TestProgressGetIsAnAgentsOwn(t *testing.T) {
	r := newRig(t)
	dbpkg.SetMeta(r.db, dbpkg.AgentProgressKey("alice"), "halfway")
	got := r.result(r.one(r.server("alice"), "progress.get", `{}`))
	if got["note"] != "halfway" {
		t.Fatal(got)
	}
	if d := r.errData(r.one(r.server(""), "progress.get", `{}`)); d["code"] != "refused" {
		t.Fatal(d)
	}
}
