package db

import "testing"

func TestTaskStateLadder(t *testing.T) {
	q := open(t)
	tid := seedTask(t, q, "b", "a")
	state := func() string {
		task, _ := GetTask(q, tid)
		s, err := TaskState(q, task)
		if err != nil {
			t.Fatal(err)
		}
		return s
	}
	if got := state(); got != "draft" {
		t.Fatal(got)
	}
	SetTaskQueued(q, tid, "a")
	if got := state(); got != "queued" {
		t.Fatal(got)
	}
	rid := ask(t, q, "a", "b", "work", "task")
	LinkRequest(q, tid, rid)
	if got := state(); got != "doing" {
		t.Fatalf("a task with work out is doing: %s", got)
	}
	// an owner holds one task: a second with work out is queued behind it
	other := seedTask(t, q, "b", "a")
	SetCurrentTask(q, "b", tid, true)
	r2 := ask(t, q, "a", "b", "more", "task")
	LinkRequest(q, other, r2)
	o, _ := GetTask(q, other)
	if s, _ := TaskState(q, o); s != "queued" {
		t.Fatalf("behind the current one: %s", s)
	}
	if yes, _ := IsDoing(q, other); yes {
		t.Fatal("not the current task")
	}
	if yes, _ := IsDoing(q, tid); !yes {
		t.Fatal("the current task is doing")
	}
	MarkTaskDone(q, tid, "ok", "reason")
	if got := state(); got != "done" {
		t.Fatal(got)
	}
	DiscardTask(q, other, "a", "no")
	o, _ = GetTask(q, other)
	if s, _ := TaskState(q, o); s != "discarded" {
		t.Fatal(s)
	}
}

func TestWaitsAndTheCurrentTask(t *testing.T) {
	q := open(t)
	cur := seedTask(t, q, "b", "a")
	SetCurrentTask(q, "b", cur, true)
	if w, _ := BlockWait(q, "b"); w != "" {
		t.Fatal(w)
	}
	blocker := seedTask(t, q, "c", "a")
	AddBlock(q, cur, blocker)
	if w, _ := BlockWait(q, "b"); w != "blocked by T"+itoa(blocker)+" (c)" {
		t.Fatal(w)
	}
	child, _ := NewTask(q, "child", "c", "b", cur, DefaultPriority, "", 0)
	AddBlock(q, cur, child)
	if w, _ := ChildWait(q, "b"); w != "waiting for T"+itoa(child)+" (c)" {
		t.Fatal(w)
	}
	if yes, _ := CurrentTaskStillAsking(q, "b"); !yes {
		t.Fatal("held on a child")
	}
	RemoveBlock(q, cur, child)
	DiscardTask(q, child, "b", "x")
	rid := ask(t, q, "a", "b", "w", "task")
	LinkRequest(q, cur, rid)
	if yes, _ := CurrentTaskStillAsking(q, "b"); !yes {
		t.Fatal("its own request is out")
	}
	// waiting tasks: blocked ones are skipped, a queued one is listed with no request when never delivered
	SetTaskQueued(q, blocker, "a")
	q.Exec("UPDATE tasks SET owner = 'b' WHERE id = ?", blocker)
	next := seedTask(t, q, "b", "a")
	SetTaskQueued(q, next, "a")
	waiting, err := WaitingTasks(q, "b")
	if err != nil {
		t.Fatal(err)
	}
	var ids []int64
	for _, w := range waiting {
		ids = append(ids, w.Task.ID)
		if w.Task.ID == next && w.Req != nil {
			t.Fatal("never delivered, so no request")
		}
	}
	if len(ids) != 2 || ids[0] != blocker || ids[1] != next { // cur is blocked by blocker, so it is skipped
		t.Fatalf("%v", ids)
	}
	if n, _ := NewestRequest(q); n != rid {
		t.Fatal(n)
	}
	if n, _ := ResetAfter(q, cur); n != 0 {
		t.Fatal(n)
	}
}

func TestMemoSummaryAndHandoffRecord(t *testing.T) {
	q := open(t)
	tid := seedTask(t, q, "b", "a")
	if s, _ := MemoSummary(q, tid, 500); s != "" {
		t.Fatal(s)
	}
	q.Exec("INSERT INTO task_memos(task_id, author, body, created_at) VALUES(?, 'a', 'first\nline', 1759900000.123)", tid)
	s, _ := MemoSummary(q, tid, 500)
	if s == "" || s[:8] != "Memos - " || len(s) < 20 {
		t.Fatal(s)
	}
	if cut, _ := MemoSummary(q, tid, 12); len([]rune(cut)) != 13 {
		t.Fatalf("%q", cut)
	}

	if h, _ := GetHandoff(q, "a"); h != "" {
		t.Fatal(h)
	}
	SetHandoff(q, "a", "note")
	if h, _ := GetHandoff(q, "a"); h != "note" {
		t.Fatal(h)
	}
	ClearHandoff(q, "a")
	if h, _ := GetHandoff(q, "a"); h != "" {
		t.Fatal(h)
	}
	if k, _ := LastHandoffTrigger(q, "a"); k != "" {
		t.Fatal(k)
	}
	q.Exec("INSERT INTO handoff(id, agent, trigger_kind, step, state, updated) VALUES('h1', 'a', 'usage', 'requested', 'in_flight', 5)")
	if k, _ := LastHandoffTrigger(q, "a"); k != "usage" {
		t.Fatal(k)
	}
}

func TestWaitingTasksOffersARequestedTaskSkipsOneAnsweredAndOffersItAgainAfterAReset(t *testing.T) {
	q := open(t)
	tid := seedTask(t, q, "b", "a")
	SetTaskQueued(q, tid, "a")
	// never delivered: offered with no request
	w, _ := WaitingTasks(q, "b")
	if len(w) != 1 || w[0].Req != nil {
		t.Fatalf("%+v", w)
	}
	// its own request is out: offered with it, and the request counts as asking
	rid := ask(t, q, "a", "b", "work", "task")
	LinkRequest(q, tid, rid)
	w, _ = WaitingTasks(q, "b")
	if len(w) != 1 || w[0].Req == nil || w[0].Req.ID != rid || !Asks(w[0].Req) {
		t.Fatalf("%+v", w)
	}
	// delivered and answered: the task no longer has work out
	CloseIfQueued(q, rid, "done", "", CloseOpts{})
	EndHeld(q, tid, "")
	RefreshSent(q, tid)
	if w, _ = WaitingTasks(q, "b"); len(w) != 0 {
		t.Fatalf("a task whose request reached the owner and was answered is sent again: %+v", w)
	}
	// a reset event that covers a later request offers it again
	newest, _ := NewestRequest(q)
	AddEvent(q, "a", "queue_task", KV{"task_id", tid}, KV{"after_request", newest + 1})
	w, _ = WaitingTasks(q, "b")
	if len(w) != 1 || w[0].Req != nil {
		t.Fatalf("a reset did not offer it again: %+v", w)
	}
	// a request that never reached its owner does not count as delivered
	t2 := seedTask(t, q, "c", "a")
	SetTaskQueued(q, t2, "a")
	denied, _ := AddRequest(q, NewRequest{Requester: "a", Target: "c", Body: "x", Kind: "task", State: "denied"})
	LinkRequest(q, t2, denied)
	if w, _ := WaitingTasks(q, "c"); len(w) != 1 {
		t.Fatalf("a denied request hides the task: %+v", w)
	}
	if !neverReached("withdrawn") || neverReached("landed") {
		t.Fatal("never-reached states")
	}
}
