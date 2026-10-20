package db

import "testing"

func ask(t *testing.T, q Q, from, to, body, kind string) int64 {
	t.Helper()
	rid, err := AddRequest(q, NewRequest{Requester: from, Target: to, Body: body, Kind: kind})
	if err != nil {
		t.Fatal(err)
	}
	return rid
}

func TestAnAskQueuesInOrderAndASayIsNeverTheHead(t *testing.T) {
	q := open(t)
	say := ask(t, q, "a", "b", "psst", "say")
	first := ask(t, q, "a", "b", "one", "ask")
	second := ask(t, q, "c", "b", "two", "ask")
	head, _ := HeadOfQueue(q, "b", 0)
	if head == nil || head.ID != first {
		t.Fatalf("head %+v", head)
	}
	if h, _ := HeadOfQueue(q, "b", first); h == nil || h.ID != second {
		t.Fatalf("head after skipping %+v", h)
	}
	pend, _ := SendNowPending(q, "b")
	if len(pend) != 1 || pend[0].ID != say {
		t.Fatalf("a say is told at once: %+v", pend)
	}
	PromoteToFront(q, second)
	if h, _ := HeadOfQueue(q, "b", 0); h.ID != second {
		t.Fatalf("promoted request is not first: %d", h.ID)
	}
	if r, _ := GetRequest(q, first); r.ChainID.String != itoa(first) {
		t.Fatalf("a request with no parent starts its own chain: %q", r.ChainID.String)
	}
}

func TestDeliveryThenLandingMovesARequestOutOfTheQueue(t *testing.T) {
	q := open(t)
	rid := ask(t, q, "a", "b", "hi", "ask")
	if ok, _ := MarkDelivered(q, rid); !ok {
		t.Fatal("not delivered")
	}
	MarkTyped(q, rid, "s1")
	if rows, _ := AwaitingLanding(q, "b"); len(rows) != 1 {
		t.Fatal("typed, not landed, is awaited")
	}
	OweAgain(q, rid)
	if r, _ := GetRequest(q, rid); r.DeliveredAt.Valid || r.TypedAt.Valid {
		t.Fatal("owed again keeps its marks")
	}
	MarkDelivered(q, rid)
	MarkTyped(q, rid, "s1")
	MarkLanded(q, rid)
	r, _ := GetRequest(q, rid)
	if r.State != "landed" || !r.LandedAt.Valid {
		t.Fatalf("%+v", r)
	}
	if ok, _ := MarkDelivered(q, rid); ok {
		t.Fatal("a landed request is delivered again")
	}
}

// The CLI's echo can land a request before the send records its typing: the typing is still recorded, once.
func TestATypingRecordedAfterTheLandingIsKeptOnce(t *testing.T) {
	q := open(t)
	rid := ask(t, q, "a", "b", "hi", "say")
	MarkDelivered(q, rid)
	MarkLanded(q, rid)
	MarkTyped(q, rid, "s1")
	first, _ := GetRequest(q, rid)
	if !first.TypedAt.Valid || first.TypedSess.String != "s1" {
		t.Fatalf("%+v", first)
	}
	MarkTyped(q, rid, "s2")
	if r, _ := GetRequest(q, rid); r.TypedSess.String != "s1" {
		t.Fatalf("a landed request's typing is not moved: %+v", r)
	}
}

func TestClosingARequestKeepsItsVerdictAsTheReasonOnceAndWritesNoAnswerRow(t *testing.T) {
	q := open(t)
	rid := ask(t, q, "a", "b", "q?", "ask")
	ok, err := CloseIfQueued(q, rid, "done", "yes")
	if !ok || err != nil {
		t.Fatal(ok, err)
	}
	if ok, _ := CloseIfQueued(q, rid, "done", "again"); ok {
		t.Fatal("closed twice")
	}
	if r, _ := GetRequest(q, rid); r.State != "landed" || r.Reason.String != "yes" {
		t.Fatalf("%+v", r)
	}
	var n int
	q.QueryRow("SELECT COUNT(*) FROM requests WHERE requester = 'b' AND target = 'a'").Scan(&n)
	if n != 0 {
		t.Fatalf("%d answer rows: a close writes none", n)
	}
}

func TestADecisionTaskStaysOpenWhenItsCardLeavesTheQueue(t *testing.T) {
	q := open(t)
	tid, _ := NewTask(q, "decide", Human, "a", 0, DefaultPriority, "", 0, "")
	card := ask(t, q, "a", Human, "which?", "ask")
	LinkRequest(q, tid, card)
	if ids, _ := OpenDecisionCardIDs(q, "a", "", nil); len(ids) != 1 || ids[0] != card {
		t.Fatal(ids)
	}
	CloseIfQueued(q, card, "done", "the first")
	if task, _ := GetTask(q, tid); task.Ended() {
		t.Fatalf("a decision ends by task.done, not by its card: %+v", task)
	}
}

func TestATaskDeliveryIsHeldUntilItsTaskEnds(t *testing.T) {
	q := open(t)
	tid, _ := NewTask(q, "work", "b", "a", 0, DefaultPriority, "", 0, "")
	rid := ask(t, q, "a", "b", "do it", "task")
	LinkRequest(q, tid, rid)
	if task, _ := GetTask(q, tid); !task.SentAt.Valid {
		t.Fatal("a queued delivery marks the task sent")
	}
	MarkDelivered(q, rid)
	MarkLanded(q, rid)
	if rows, _ := QueuedFor(q, "b"); len(rows) != 1 {
		t.Fatalf("the held delivery leaves the queue: %d", len(rows))
	}
	if r, _ := LiveDelivery(q, tid, "b"); r == nil || r.ID != rid {
		t.Fatal("no live delivery")
	}
	if task, _ := GetTask(q, tid); !task.DeliveredAt.Valid {
		t.Fatal("landing stamps the task delivered")
	}
	MarkTaskDone(q, tid, "abc", "reason")
	if rows, _ := QueuedFor(q, "b"); len(rows) != 0 {
		t.Fatalf("an ended task stays in the queue: %d", len(rows))
	}
}

func TestSettlingLeftoverDeliveriesClosesOnlyThoseWhoseTasksAllEnded(t *testing.T) {
	q := open(t)
	t1, _ := NewTask(q, "x", "b", "a", 0, DefaultPriority, "", 0, "")
	r1 := ask(t, q, "a", "b", "x", "task")
	LinkRequest(q, t1, r1)
	t2, _ := NewTask(q, "y", "b", "a", 0, DefaultPriority, "", 0, "")
	r2 := ask(t, q, "a", "b", "y", "task")
	LinkRequest(q, t2, r2)
	MarkTaskDone(q, t1, "ok", "reason")
	done, err := SettleLeftoverDeliveries(q)
	if err != nil || len(done) != 1 || done[0] != r1 {
		t.Fatal(done, err)
	}
	if ok, _ := IsStillQueued(q, r2); !ok {
		t.Fatal("a live delivery was closed")
	}
}

func TestNoticesAreBornLandedAndWaitForADigest(t *testing.T) {
	q := open(t)
	n, _ := AddRequest(q, NewRequest{Requester: NoticeRequester, Target: "b", Body: "T1 done by x: ok", Kind: "say", Notice: true})
	r, _ := GetRequest(q, n)
	if r.State != "landed" || !r.LandedAt.Valid || r.DeliveredAt.Valid {
		t.Fatalf("%+v", r)
	}
	waiting, _ := AgentsWithNotices(q)
	if len(waiting) != 1 || waiting[0].Agent != "b" || waiting[0].Since != 1000 {
		t.Fatal(waiting)
	}
	MarkNoticesDigested(q, []int64{n})
	if p, _ := PendingNotices(q, "b"); len(p) != 0 {
		t.Fatal("still pending")
	}
}

func TestAStandingDenialIsTheSameMessageRefusedBefore(t *testing.T) {
	q := open(t)
	ask(t, q, "a", "b", "one", "ask")
	d, _ := AddRequest(q, NewRequest{Requester: "a", Target: "b", Body: "dup", State: "denied"})
	if id, _ := StandingDenial(q, "a", "b", "dup", "", 0); id != d {
		t.Fatal(id)
	}
	if id, _ := StandingDenial(q, "a", "b", "dup", "f.txt", 0); id != 0 {
		t.Fatal("a different file is a different message")
	}
}

func TestPendingMessagesAreOwedUntilDeleted(t *testing.T) {
	q := open(t)
	id, _ := AddPending(q, "a", "hello", 5)
	AddPending(q, "b", "yo", 6)
	if rows, _ := ListPending(q, "a"); len(rows) != 1 || rows[0].Text != "hello" {
		t.Fatal(rows)
	}
	if rows, _ := ListPending(q, ""); len(rows) != 2 {
		t.Fatal(rows)
	}
	DeletePending(q, id)
	if p, _ := GetPending(q, id); p != nil {
		t.Fatal("typed and gone")
	}
}

func TestBlocksOpenUpdateAndClear(t *testing.T) {
	q := open(t)
	BlockOpen(q, "a", "dialog", "d", "w", 10, 20, "tick", "t")
	BlockUpdate(q, "a", "login", "d2", "w", 30, "t2")
	b, _ := BlockGet(q, "a")
	if b == nil || b.Kind != "login" || b.Seen != 30 || b.Since != 10 {
		t.Fatalf("%+v", b)
	}
	BlockSetRequest(q, []string{"a"}, 4)
	if b, _ := BlockGet(q, "a"); b.RequestID.Int64 != 4 {
		t.Fatal("request not set")
	}
	BlockClear(q, "a")
	if b, _ := BlockGet(q, "a"); b != nil {
		t.Fatal("not cleared")
	}
}

func TestAHandoffStartsInFlightAdvancesAndLeavesTheOldestFirst(t *testing.T) {
	q := open(t)
	if h, err := InFlightFor(q, "a"); err != nil || h != nil {
		t.Fatalf("%v %v", h, err)
	}
	if err := StartHandoff(q, "h1", "a", "s1", "idle", 500); err != nil {
		t.Fatal(err)
	}
	h, err := InFlightFor(q, "a")
	if err != nil || h == nil || h.ID != "h1" || h.Step != "requested" || h.State != "in_flight" || h.SessionID.String != "s1" || h.Deadline.Int64 != 500 {
		t.Fatalf("%+v %v", h, err)
	}
	if err := AdvanceHandoff(q, "h1", "captured", "", "note"); err != nil {
		t.Fatal(err)
	}
	h, _ = InFlightFor(q, "a")
	if h.Step != "captured" || h.State != "in_flight" || h.Detail.String != "note" {
		t.Fatalf("an empty state keeps the one there: %+v", h)
	}
	if err := AdvanceHandoff(q, "h1", "done", "finished", ""); err != nil {
		t.Fatal(err)
	}
	if h, _ := InFlightFor(q, "a"); h != nil {
		t.Fatalf("a finished handoff is not in flight: %+v", h)
	}
	// Starting the same id again replaces it.
	if err := StartHandoff(q, "h1", "a", "", "idle", 600); err != nil {
		t.Fatal(err)
	}
	if h, _ := InFlightFor(q, "a"); h == nil || h.Deadline.Int64 != 600 || h.SessionID.Valid {
		t.Fatalf("%+v", h)
	}
}
