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
	if got := state(); got != "queued" {
		t.Fatalf("work out is not doing until its owner holds it: %s", got)
	}
	SetCurrentTask(q, "b", tid, true)
	if got := state(); got != "doing" {
		t.Fatalf("the task its owner holds, with work out, is doing: %s", got)
	}
	blocker := seedTask(t, q, "c", "a")
	AddBlock(q, tid, blocker)
	if got := state(); got != "queued" {
		t.Fatalf("a blocked task is queued, held or not: %s", got)
	}
	RemoveBlock(q, tid, blocker)
	// an owner holds one task: a second with work out is queued behind it
	other := seedTask(t, q, "b", "a")
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
	child, _ := NewTask(q, "child", "c", "b", cur, DefaultPriority, "", 0, "")
	AddBlock(q, cur, child)
	if w, _ := ChildWait(q, "b"); w != "waiting for T"+itoa(child)+" (c)" {
		t.Fatal(w)
	}
	rid := ask(t, q, "a", "b", "w", "task")
	LinkRequest(q, cur, rid)
	if yes, _ := CurrentTaskStillAsking(q, "b"); yes {
		t.Fatal("a blocked current task does not hold its owner, child or not")
	}
	RemoveBlock(q, cur, child)
	DiscardTask(q, child, "b", "x")
	RemoveBlock(q, cur, blocker)
	if yes, _ := CurrentTaskStillAsking(q, "b"); !yes {
		t.Fatal("its own request is out")
	}
	AddBlock(q, cur, blocker)
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
	CloseIfQueued(q, rid, "done", "")
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

func TestAnAgentHasAtMostOneDoing(t *testing.T) {
	q := open(t)
	parent := seedTask(t, q, "b", "a")
	var tids []int64
	for i := 0; i < 3; i++ {
		tid := seedTask(t, q, "b", "a")
		LinkRequest(q, tid, ask(t, q, "a", "b", "work", "task"))
		tids = append(tids, tid)
	}
	child, _ := NewTask(q, "child", "c", "b", parent, DefaultPriority, "", 0, "")
	LinkRequest(q, child, ask(t, q, "b", "c", "look", "task"))
	doing := func() (n int) {
		for _, tid := range append([]int64{parent}, tids...) {
			if yes, _ := IsDoing(q, tid); yes {
				n++
			}
		}
		return n
	}
	if n := doing(); n != 0 {
		t.Fatalf("with nothing held, %d read doing", n)
	}
	SetCurrentTask(q, "b", tids[1], true)
	if n := doing(); n != 1 {
		t.Fatalf("%d doing", n)
	}
	MarkTaskDone(q, tids[1], "ok", "reason")
	if n := doing(); n != 0 {
		t.Fatalf("with its current task done, %d read doing", n)
	}
}

func TestDeliveryAndTheCurrentTaskAgreeOnWhatIsInTheWay(t *testing.T) {
	q := open(t)
	cur := seedTask(t, q, "w", "a")
	LinkRequest(q, cur, ask(t, q, "a", "w", "first", "task"))
	SetCurrentTask(q, "w", cur, true)
	next := seedTask(t, q, "w", "a")
	rid := ask(t, q, "a", "w", "next", "task")
	LinkRequest(q, next, rid)
	n, _ := GetTask(q, next)
	if way, _ := HoldsCurrent(q, n); way {
		t.Fatal("an open current task is in the way")
	}
	AddBlock(q, cur, seedTask(t, q, "x", "a"))
	if way, _ := HoldsCurrent(q, n); !way {
		t.Fatal("a blocked current task is not in the way")
	}
	MarkDelivered(q, rid)
	if held, _, _ := CurrentTask(q, "w"); held != next {
		t.Fatalf("current is T%d, want the delivered T%d", held, next)
	}
	if s, _ := TaskState(q, n); s != "doing" {
		t.Fatalf("the delivered task reads %s", s)
	}
}
