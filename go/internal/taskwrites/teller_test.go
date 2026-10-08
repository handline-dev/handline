package taskwrites

import (
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
)

// fakeTeller records what the writes leave for the telling layer.
type fakeTeller struct {
	memos     []string
	queued    []string
	discarded []string
	told      []db.Notice
	freed     []string
	forget    []db.InboxFile
}

func (f *fakeTeller) MemoChanged(tid int64, by, what, text string) {
	f.memos = append(f.memos, fmt.Sprintf("T%d %s %s %q", tid, by, what, text))
}
func (f *fakeTeller) QueueDelivered(tid int64, by string, queued bool) string {
	f.queued = append(f.queued, fmt.Sprintf("T%d %s %v", tid, by, queued))
	return fmt.Sprintf("delivered T%d", tid)
}
func (f *fakeTeller) Discarded(owner string, told []db.Notice, freed []string, forget []db.InboxFile) {
	f.discarded = append(f.discarded, owner)
	f.told, f.freed, f.forget = told, freed, forget
}

func TestADiscardHandsItsSaysItsFreedOwnersAndItsClosedInboxFilesToTheTeller(t *testing.T) {
	sdb, mk := discardEnv(t)
	for _, q := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'worker', 'manager', 1), (2, 'u', 'other', 'other', 1)",
		"INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES(2, 1, 1)",
		"INSERT INTO requests(id, ts, requester, target, body, kind, state, edge_ok) VALUES(5, 1, 'manager', 'worker', 'do t', 'task', 'queued', 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 5)",
	} {
		if _, err := sdb.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	// the request still queued on it refuses a plain discard: withdraw it first
	if _, err := sdb.Exec("UPDATE requests SET state = 'landed', delivered_at = 1, landed_at = 1 WHERE id = 5"); err != nil {
		t.Fatal(err)
	}
	f := &fakeTeller{}
	s := mk("manager", f)
	r := call(t, s, `{"jsonrpc":"2.0","id":2,"method":"task.discard","params":{"task":1,"reason":"not needed","request_id":"d1"}}`)
	if r[0]["error"] != nil {
		t.Fatal(r[0])
	}
	if !reflect.DeepEqual(f.discarded, []string{"worker"}) || !reflect.DeepEqual(f.freed, []string{"other"}) {
		t.Fatalf("%v %v", f.discarded, f.freed)
	}
	var texts []string
	for _, n := range f.told {
		texts = append(texts, n.To+": "+n.Text)
	}
	got := strings.Join(texts, " | ")
	if !strings.Contains(got, "worker: T1 discarded by manager: not needed") || !strings.Contains(got, "other: task T2 is unblocked - T1, its last open blocker, discarded") {
		t.Fatalf("%s", got)
	}
	// a discard that refuses tells nothing
	sdb.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(3, 'v', 'other', 'other', 1)")
	f2 := &fakeTeller{}
	s2 := mk("other", f2)
	if r := call(t, s2, `{"jsonrpc":"2.0","id":2,"method":"task.discard","params":{"task":3,"reason":"x","request_id":"d2"}}`); r[0]["error"] != nil {
		t.Fatal(r[0])
	}
	if len(f2.discarded) != 1 {
		t.Fatalf("%v", f2.discarded)
	}
	if r := call(t, s2, `{"jsonrpc":"2.0","id":3,"method":"task.discard","params":{"task":3,"reason":"again","request_id":"d3"}}`); r[0]["error"] == nil || len(f2.discarded) != 1 {
		t.Fatalf("a refused discard told the teller: %v", f2.discarded)
	}
}

func TestMemoWritesTellTheHolderThroughTheTeller(t *testing.T) {
	sdb, mk := discardEnv(t)
	sdb.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'worker', 'manager', 1)")
	f := &fakeTeller{}
	s := mk("manager", f)
	call(t, s, `{"jsonrpc":"2.0","id":1,"method":"task.memo.add","params":{"task":1,"text":"check the cache","request_id":"m1"}}`)
	call(t, s, `{"jsonrpc":"2.0","id":2,"method":"task.memo.edit","params":{"task":1,"memo":1,"text":"check the disk","request_id":"m2"}}`)
	call(t, s, `{"jsonrpc":"2.0","id":3,"method":"task.memo.delete","params":{"task":1,"memo":1,"request_id":"m3"}}`)
	want := []string{`T1 manager added "check the cache"`, `T1 manager edited "check the disk"`, `T1 manager deleted ""`}
	if !reflect.DeepEqual(f.memos, want) {
		t.Fatalf("%q", f.memos)
	}
	// a memo that is refused is not told
	f.memos = nil
	call(t, s, `{"jsonrpc":"2.0","id":4,"method":"task.memo.add","params":{"task":99,"text":"x","request_id":"m4"}}`)
	if len(f.memos) != 0 {
		t.Fatalf("%q", f.memos)
	}
}

func TestQueueRepliesWithWhatDeliveryDidForEachTask(t *testing.T) {
	sdb, mk := discardEnv(t)
	sdb.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'worker', 'manager', 1), (2, 'u', 'worker', 'manager', 1)")
	f := &fakeTeller{}
	s := mk("human", f)
	r := call(t, s, `{"jsonrpc":"2.0","id":1,"method":"task.queue","params":{"tasks":[1,2],"request_id":"q1"}}`)
	if r[0]["error"] != nil {
		t.Fatal(r[0])
	}
	if got := r[0]["result"].(map[string]any)["said"]; got != "delivered T1\ndelivered T2" {
		t.Fatalf("%v", got)
	}
	if !reflect.DeepEqual(f.queued, []string{"T1 human true", "T2 human true"}) {
		t.Fatalf("%v", f.queued)
	}
}
