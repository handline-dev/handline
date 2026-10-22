package handoff

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
)

// ann holds two tasks sent to it (one waiting on the other) and a draft not yet sent; it talked with the founder and its lead boss, and
// a third agent wrote to it too. With no record its own progress note comes along; a record a week old is flagged.
func TestWhereListsTheTasksTheLastWordsWithTheFounderAndTheLeadAndTheProgress(t *testing.T) {
	w := newWorld(t)
	q := w.DB
	first, _ := db.NewTask(q, "build the pace cap", "ann", "boss", 0, 1, "", 0, "")
	second, _ := db.NewTask(q, "rebuild the daemon", "ann", "boss", 0, 2, "", 0, "")
	db.NewTask(q, "not sent yet", "ann", "boss", 0, 2, "", 0, "")
	for _, tid := range []int64{first, second} {
		if _, err := db.SetTaskQueued(q, tid, "boss"); err != nil {
			t.Fatal(err)
		}
	}
	held, _ := db.NewTask(q, "the one in hand", "ann", "boss", 0, 3, "", 0, "")
	if _, err := db.SetTaskQueued(q, held, "boss"); err != nil {
		t.Fatal(err)
	}
	rid, err := db.AddRequest(q, db.NewRequest{Requester: "boss", Target: "ann", Body: "the one in hand", Kind: "task"})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.LinkRequest(q, held, rid); err != nil {
		t.Fatal(err)
	}
	if err := db.SetCurrentTask(q, "ann", held, true); err != nil {
		t.Fatal(err)
	}
	if err := db.AddBlock(q, second, first); err != nil {
		t.Fatal(err)
	}
	db.AddMemo(q, first, "boss", "an older memo")
	db.AddMemo(q, first, "boss", "start after the contract lands\nsecond line")
	for _, m := range []db.NewRequest{{Requester: db.Human, Target: "ann", Body: "do it right"}, {Requester: "ann", Target: "boss", Body: "blocked on the contract"},
		{Requester: "zed", Target: "ann", Body: "an aside"}} {
		if _, err := db.AddRequest(q, m); err != nil {
			t.Fatal(err)
		}
	}
	if err := db.SetMeta(q, taskProgressKey(held), "done: slice 1; left: slice 2"); err != nil {
		t.Fatal(err)
	}
	now := time.Unix(w.now, 0)

	got, err := WhereText(q, "ann", "boss", "", now)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(got, "# Where you were (the daemon's record)\nyour open tasks:\n  T"+fmt.Sprint(held)+" doing P3 - the one in hand\n") {
		t.Errorf("the task in hand comes first, whatever its priority:\n%s", got)
	}
	for _, want := range []string{
		"P1 - build the pace cap", "last memo, boss", ": start after the contract lands",
		fmt.Sprintf("P2 - rebuild the daemon (waits on T%d)", first),
		"last messages with the founder and boss", "human -> you: do it right", "you -> boss: blocked on the contract",
		"done: slice 1; left: slice 2", "hl inbox --all",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("missing %q:\n%s", want, got)
		}
	}
	for _, not := range []string{"not sent yet", "an older memo", "second line", "an aside", "days old"} {
		if strings.Contains(got, not) {
			t.Errorf("has %q:\n%s", not, got)
		}
	}

	old := capturedPrefix + now.Add(-7*24*time.Hour).Format(capturedLayout)
	got, _ = WhereText(q, "ann", "boss", "# handoff · ann\n"+old+"\nrequest: none in progress", now)
	if !strings.Contains(got, "Your handoff above is 7 days old") || strings.Contains(got, "done: slice 1") {
		t.Errorf("a week-old record is flagged, and its progress is the record's to carry:\n%s", got)
	}
	fresh := capturedPrefix + now.Add(-time.Hour).Format(capturedLayout)
	if got, _ = WhereText(q, "ann", "boss", fresh, now); strings.Contains(got, "days old") {
		t.Errorf("an hour-old record is flagged:\n%s", got)
	}
}

// An agent the founder leads, with nothing on record, is told it has no tasks and no messages; once the founder writes, the founder
// alone is named.
func TestWhereForAnAgentTheFounderLeads(t *testing.T) {
	w := newWorld(t)
	got, err := WhereText(w.DB, "ann", "", "", time.Unix(w.now, 0))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(got, "your open tasks: none") || strings.Contains(got, "last messages") {
		t.Errorf("%s", got)
	}
	if _, err := db.AddRequest(w.DB, db.NewRequest{Requester: db.Human, Target: "ann", Body: "hello"}); err != nil {
		t.Fatal(err)
	}
	if got, _ = WhereText(w.DB, "ann", "", "", time.Unix(w.now, 0)); !strings.Contains(got, "last messages with the founder, newest first:\n  ") {
		t.Errorf("%s", got)
	}
}
