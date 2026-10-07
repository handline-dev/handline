package db

import (
	"testing"
)

func seedTask(t *testing.T, q Q, owner, opener string) int64 {
	t.Helper()
	tid, err := NewTask(q, "work", owner, opener, 0, DefaultPriority, "", 0)
	if err != nil {
		t.Fatal(err)
	}
	return tid
}

// Each reader names its own columns, so a wrong count only shows when the statement runs against the schema: run each once and read a row back.
func TestEveryRowReaderScansItsColumnsAgainstTheSchema(t *testing.T) {
	q := open(t)
	SetAgentState(q, "a", "IDLE", 0, "")
	if rows, err := AllAgentStates(q); err != nil || len(rows) != 1 || rows[0].Agent != "a" {
		t.Fatal(rows, err)
	}
	BlockOpen(q, "a", "dialog", "d", "w", 10, 20, "tick", "t")
	if rows, err := Blocks(q); err != nil || len(rows) != 1 || rows[0].Title != "t" {
		t.Fatal(rows, err)
	}
	PressAdd(q, "a", 11, "title", "pressed", "n")
	if rows, err := PressesSince(q, "a", 0); err != nil || len(rows) != 1 || rows[0].Outcome != "pressed" || rows[0].Note != "n" {
		t.Fatal(rows, err)
	}

	parent := seedTask(t, q, "b", "a")
	blocker := seedTask(t, q, "c", "a")
	AddBlock(q, parent, blocker)
	if rows, err := TaskBlockers(q, parent); err != nil || len(rows) != 1 || rows[0].ID != blocker {
		t.Fatal(rows, err)
	}
	if ids, _ := BlocksOnIDs(q, parent); len(ids) != 1 || ids[0] != blocker {
		t.Fatal(ids)
	}
	if ids, _ := TasksBlockedBy(q, blocker); len(ids) != 1 || ids[0] != parent {
		t.Fatal(ids)
	}
	if ok, _ := RemoveBlock(q, parent, blocker); !ok {
		t.Fatal("block not removed")
	}
	if rows, _ := TaskBlockers(q, parent); len(rows) != 0 {
		t.Fatal("removed block still blocks")
	}
	child, _ := NewTask(q, "child", "b", "a", parent, DefaultPriority, "", 0)
	if rows, err := OpenChildTasks(q, parent); err != nil || len(rows) != 1 || rows[0].ID != child || rows[0].ParentTask.Int64 != parent {
		t.Fatal(rows, err)
	}
	if rows, err := OpenTasks(q); err != nil || len(rows) != 3 {
		t.Fatal(len(rows), err)
	}

	rid := ask(t, q, "a", "b", "work", "task")
	LinkRequest(q, parent, rid)
	if rows, err := TasksForRequest(q, rid); err != nil || len(rows) != 1 || rows[0].ID != parent || rows[0].Title != "work" {
		t.Fatal(rows, err)
	}
	if rows, err := ChainMembers(q, itoa(rid)); err != nil || len(rows) != 1 || rows[0].ID != rid {
		t.Fatal(rows, err)
	}
	if rows, err := UndeliveredFor(q, "b"); err != nil || len(rows) != 1 || rows[0].ID != rid {
		t.Fatal(rows, err)
	}
	founder := ask(t, q, Human, "b", "go", "ask")
	if rows, err := FounderPending(q, "b"); err != nil || len(rows) != 1 || rows[0].ID != founder {
		t.Fatal(rows, err)
	}
	reply, _ := AddRequest(q, NewRequest{Requester: "a", Target: "b", Body: "more", Kind: "ask", ParentID: rid, ChainID: itoa(rid)})
	if rows, err := FollowupsPending(q, "b", rid); err != nil || len(rows) != 1 || rows[0].ID != reply {
		t.Fatal(rows, err)
	}
	// a queued delivery whose task has ended is a leftover
	MarkTaskDone(q, parent, "ok", "reason")
	if rows, err := LeftoverDeliveries(q); err != nil || len(rows) != 1 || rows[0].ID != rid {
		t.Fatal(rows, err)
	}
}

func TestEveryWriterChangesItsRow(t *testing.T) {
	q := open(t)
	SetTask(q, "a", "do this")
	if s, _ := GetAgentState(q, "a"); s.Task.String != "do this" {
		t.Fatalf("%+v", s)
	}
	SetAccount(q, "a", "acct", "assumed")
	SetLaunchFingerprint(q, "a", "fp", `{"x": 1}`)
	SetClosed(q, "a", "sess")
	ForgetResume(q, "a")
	SetNotifiedSession(q, "a", "s9")
	s, _ := GetAgentState(q, "a")
	if s.Account.String != "acct" || s.AccountSource.String != "assumed" || s.LaunchFingerprint.String != "fp" || s.LaunchParts.String != `{"x": 1}` ||
		s.ResumeSession.Valid || !s.ClosedAt.Valid || s.NotifiedSession.String != "s9" {
		t.Fatalf("%+v", s)
	}
	if got, _ := NotifiedSession(q, "a"); got != "s9" {
		t.Fatal(got)
	}
	BumpCounter(q, "a", "wake_failures", 4)
	ResetCounter(q, "a", "wake_failures")
	if s, _ := GetAgentState(q, "a"); s.WakeFailures != 0 {
		t.Fatal(s.WakeFailures)
	}
	if ResetCounter(q, "a", "agent") == nil {
		t.Fatal("any column reset")
	}

	tid := seedTask(t, q, "b", "a")
	if ok, _ := SetTaskQueued(q, tid, "a"); !ok {
		t.Fatal("not queued")
	}
	if task, _ := GetTask(q, tid); task.QueuedBy.String != "a" || !task.QueuedAt.Valid {
		t.Fatalf("%+v", task)
	}
	if ok, _ := SetTaskQueued(q, tid, "a"); ok {
		t.Fatal("queued twice")
	}
	if ok, _ := SetTaskQueued(q, tid, ""); !ok {
		t.Fatal("not sent back to draft")
	}
	if task, _ := GetTask(q, tid); task.QueuedBy.Valid {
		t.Fatal("still queued")
	}

	AddEventAt(q, 77, "a", "custom", KV{"k", "v"})
	var ts int64
	q.QueryRow("SELECT ts FROM events WHERE kind = 'custom'").Scan(&ts)
	if ts != 77 {
		t.Fatal(ts)
	}

	// requests
	read := ask(t, q, "a", Human, "hello", "say")
	if err := MarkRead(q, []int64{read}); err != nil {
		t.Fatal(err)
	}
	if r, _ := GetRequest(q, read); !r.ReadAt.Valid {
		t.Fatal("not read")
	}
	notice, _ := AddRequest(q, NewRequest{Requester: NoticeRequester, Target: "b", Body: "n", Kind: "say", Notice: true})
	MarkNoticesShown(q, []int64{notice}, "s1")
	if r, _ := GetRequest(q, notice); !r.DeliveredAt.Valid || !r.TypedAt.Valid || r.TypedSess.String != "s1" {
		t.Fatalf("%+v", r)
	}
	rid := ask(t, q, "a", "b", "q", "ask")
	if ok, _ := MarkSendNow(q, rid); !ok {
		t.Fatal("not marked")
	}
	if r, _ := GetRequest(q, rid); !r.SendNow.Valid || !r.TellsNow() {
		t.Fatal("send_now not set")
	}
	MarkDelivered(q, rid)
	if ok, _ := MarkSendNow(q, rid); ok {
		t.Fatal("marked after delivery")
	}
	ClearDelivered(q, rid)
	if r, _ := GetRequest(q, rid); r.DeliveredAt.Valid {
		t.Fatal("still delivered")
	}
	ClearEdge(q, rid)
	if ok, _ := RetireIfUndelivered(q, rid, "withdrawn", "taken back"); !ok {
		t.Fatal("not retired")
	}
	if r, _ := GetRequest(q, rid); r.State != "withdrawn" || r.Answer.String != "taken back" {
		t.Fatalf("%+v", r)
	}
	asked := ask(t, q, "a", "b", "q2", "ask")
	CloseIfQueued(q, asked, "done", "yes", CloseOpts{Announced: true})
	AppendVerdict(q, asked, "also this")
	if r, _ := GetRequest(q, asked); r.Answer.String != "yes\n\nalso this" {
		t.Fatalf("%q", r.Answer.String)
	}
	if a, _ := AnswerRowOf(q, asked); a == nil || a.Requester != "b" {
		t.Fatalf("%+v", a)
	}
}

func TestSingleValueReads(t *testing.T) {
	q := open(t)
	SetTerminal(q, "a", "%1", "/w")
	AddEvent(q, "a", "Stop")
	AddEvent(q, "a", "ask")
	q.Exec("UPDATE events SET account = 'acct' WHERE kind = 'Stop'")
	id, _ := AddEvent(q, "a", "custom_kind")

	if got, _ := Terminal(q, "a"); got != "%1" {
		t.Fatal(got)
	}
	if got, _ := AgentOfTerminal(q, "%1"); got != "a" {
		t.Fatal(got)
	}
	for name, read := range map[string]func() (int64, bool, error){
		"LastActivity":        func() (int64, bool, error) { return LastActivity(q, "a") },
		"LastActivityBefore":  func() (int64, bool, error) { return LastActivityBefore(q, "a", 2000) },
		"LastActivityID":      func() (int64, bool, error) { return LastActivityID(q, "a", []string{"Stop", "ask"}) },
		"LastAccountActivity": func() (int64, bool, error) { return LastAccountActivity(q, "acct") },
		"LastEventTS":         func() (int64, bool, error) { return LastEventTS(q, "a", "custom_kind") },
		"LastEventID":         func() (int64, bool, error) { return LastEventID(q, "a", "custom_kind") },
	} {
		n, ok, err := read()
		if err != nil || !ok || n == 0 {
			t.Errorf("%s: %d %v %v", name, n, ok, err)
		}
	}
	if n, _, _ := LastEventID(q, "a", "custom_kind"); n != id {
		t.Fatal(n, id)
	}
	if _, ok, _ := LastActivity(q, "nobody"); ok {
		t.Fatal("activity for nobody")
	}

	rid := ask(t, q, "a", "b", "x", "ask")
	say := ask(t, q, "a", "b", "psst", "say")
	if n, _ := QueueDepth(q, "b"); n != 2 {
		t.Fatal(n)
	}
	if ids, _ := OpenAskIDs(q, "a"); len(ids) != 2 {
		t.Fatal(ids)
	}
	if h, _ := TaskHead(q, "b", 0); h != nil {
		t.Fatal("an ask is not a task head")
	}
	if h, _ := HeadOfQueueOrMarked(q, "b"); h == nil || h.ID != rid {
		t.Fatalf("%+v", h)
	}
	CloseIfQueued(q, rid, "done", "", CloseOpts{})
	if h, _ := HeadOfQueueOrMarked(q, "b"); h == nil || h.ID != say {
		t.Fatalf("a say-only queue still has its marked row: %+v", h)
	}
	if n, _ := UnreachedCount(q, "b"); n != 1 {
		t.Fatal(n)
	}
	if n, _ := UnreachedCount(q, "a"); n != 1 {
		t.Fatalf("the unannounced answer counts: %d", n)
	}
	RetireQueued(q, say, "withdrawn", "")
	AddEvent(q, "a", "closed_under", KV{"request_id", rid})
	if yes, _ := ClosedUnder(q, rid); !yes {
		t.Fatal("closed_under not seen")
	}
	if yes, _ := ClosedUnder(q, say); yes {
		t.Fatal("closed_under for another request")
	}
	tk, _ := GetTask(q, seedTask(t, q, "a", "a"))
	if tk.Ended() {
		t.Fatal("open task ended")
	}
	MarkTaskDone(q, tk.ID, "ok", "reason")
	if tk, _ = GetTask(q, tk.ID); !tk.Ended() {
		t.Fatal("done task not ended")
	}
}
