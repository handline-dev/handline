package cmdruns

import (
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/term"
)

func TestSettleEndsARunFromWhatItsWindowSays(t *testing.T) {
	q, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer q.Close()
	cases := []struct {
		name    string
		pane    *term.PaneRun // nil: the window is gone
		state   string
		exit    int
		seconds int64
	}{
		{"running", &term.PaneRun{}, "running", 0, -1},
		{"done exit 0", &term.PaneRun{Dead: true, Status: 0, DeadTime: 1103}, "done", 0, 3},
		{"failed exit 1", &term.PaneRun{Dead: true, Status: 1, DeadTime: 1103}, "failed", 1, 3},
		{"window gone", nil, "stopped", 0, -1},
		{"another pane under the same id", &term.PaneRun{Tag: "9:9@5"}, "stopped", 0, -1},
	}
	for i, c := range cases {
		n := int64(i)
		b := Block{Message: 7, N: n}
		if _, err := Start(q, b, "%5", 1100); err != nil {
			t.Fatal(err)
		}
		panes := map[string]term.PaneRun{}
		if c.pane != nil {
			p := *c.pane
			if p.Tag == "" {
				p.Tag = Run{Block: b.String(), Started: 1100}.Tag()
			}
			panes["%5"] = p
		}
		if err := Settle(q, panes, 2000); err != nil {
			t.Fatal(err)
		}
		runs, err := Of(q, 7)
		if err != nil {
			t.Fatal(err)
		}
		got := runs[len(runs)-1]
		for _, r := range runs { // the latest by name is not always the latest start: find this block
			if r.Block == b.String() {
				got = r
			}
		}
		if got.State != c.state || got.Seconds() != c.seconds || (got.Exit != nil && *got.Exit != c.exit) || (c.seconds >= 0) != (got.Exit != nil) {
			t.Fatalf("%s: %+v", c.name, got)
		}
		if _, err := q.Exec("DELETE FROM meta WHERE name LIKE 'run:%'"); err != nil {
			t.Fatal(err)
		}
	}
}

func TestACommandThatCannotBeAskedAboutKeepsItsRunAndARerunReplacesIt(t *testing.T) {
	q, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer q.Close()
	b := Block{Message: 3, N: 0}
	Start(q, b, "%1", 100)
	if err := Settle(q, nil, 500); err != nil { // tmux could not answer: nothing ended
		t.Fatal(err)
	}
	if runs, _ := Of(q, 3); len(runs) != 1 || runs[0].State != "running" {
		t.Fatalf("%+v", runs)
	}
	Start(q, b, "%2", 600)
	Start(q, Block{Message: 3, N: 1}, "%3", 550)
	runs, _ := Of(q, 3)
	if len(runs) != 2 || runs[0].Block != "3:1" || runs[1].Terminal != "%2" {
		t.Fatalf("a rerun is the block's last run, oldest start first: %+v", runs)
	}
	if _, ok := ParseBlock("3:x"); ok {
		t.Fatal("a block is two numbers")
	}
	if got, _ := Of(q, 4); len(got) != 0 {
		t.Fatalf("another message has none: %+v", got)
	}
}

func TestAnEndedRunIsDroppedAfterThirtyDays(t *testing.T) {
	q, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer q.Close()
	Start(q, Block{Message: 5, N: 0}, "%1", 1000)
	Start(q, Block{Message: 5, N: 1}, "%2", 1000)
	Settle(q, map[string]term.PaneRun{"%1": {Tag: "5:0@1000", Dead: true, DeadTime: 1005}, "%2": {Tag: "5:1@1000"}}, 1010)
	Settle(q, map[string]term.PaneRun{"%2": {Tag: "5:1@1000"}}, 1005+keep)
	if runs, _ := Of(q, 5); len(runs) != 2 {
		t.Fatalf("a run that ended just now stays: %+v", runs)
	}
	Settle(q, map[string]term.PaneRun{"%2": {Tag: "5:1@1000"}}, 1006+keep)
	runs, _ := Of(q, 5)
	if len(runs) != 1 || runs[0].Block != "5:1" {
		t.Fatalf("the ended one is gone, the running one stays: %+v", runs)
	}
}

func TestADeadWindowWithNoEndTimeEndsNow(t *testing.T) {
	q, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer q.Close()
	Start(q, Block{Message: 2, N: 0}, "%1", 1000)
	Settle(q, map[string]term.PaneRun{"%1": {Tag: "2:0@1000", Dead: true, Status: 2}}, 1007)
	if runs, _ := Of(q, 2); len(runs) != 1 || runs[0].Ended != 1007 || runs[0].Seconds() != 7 || runs[0].State != "failed" {
		t.Fatalf("%+v", runs)
	}
}

func TestARowThatIsNotItsOwnBlockIsNotARun(t *testing.T) {
	q, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer q.Close()
	q.Exec(`INSERT INTO meta(name, value, updated) VALUES('run:4:0', '{"block":"4:9","terminal":"%1","started":1,"state":"running"}', 1)`)
	if runs, _ := Of(q, 4); len(runs) != 0 {
		t.Fatalf("%+v", runs)
	}
}

// "r<item id>:<n>" is a reply item's block, kept under run:r<id>:<n>; its runs and a message's with the same id never mix.
func TestAReplyItemsBlockIsKeptApartFromAMessagesOfTheSameID(t *testing.T) {
	q, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer q.Close()
	b, ok := ParseBlock("r7:2")
	if !ok || !b.Reply || b.Message != 7 || b.N != 2 || b.String() != "r7:2" {
		t.Fatalf("%+v %v", b, ok)
	}
	for _, bad := range []string{"r:1", "rr7:1", "x7:1", "r7"} {
		if _, ok := ParseBlock(bad); ok {
			t.Errorf("%q is no block", bad)
		}
	}
	if m, _ := ParseBlock("7:2"); m.Reply || m.String() != "7:2" {
		t.Fatalf("a message's block: %+v", m)
	}
	if _, err := Start(q, b, "%1", 100); err != nil {
		t.Fatal(err)
	}
	if _, err := Start(q, Block{Message: 7, N: 2}, "%2", 200); err != nil {
		t.Fatal(err)
	}
	var key string
	q.QueryRow("SELECT name FROM meta WHERE name LIKE 'run:r%'").Scan(&key)
	if key != "run:r7:2" {
		t.Fatalf("kept as %q", key)
	}
	replies, err := OfReply(q, 7)
	if err != nil || len(replies) != 1 || replies[0].Terminal != "%1" {
		t.Fatalf("the reply's runs: %+v %v", replies, err)
	}
	msgs, err := Of(q, 7)
	if err != nil || len(msgs) != 1 || msgs[0].Terminal != "%2" {
		t.Fatalf("the message's runs: %+v %v", msgs, err)
	}
	all, err := Replies(q)
	if err != nil || len(all) != 1 || len(all[7]) != 1 || all[7][0].Block != "r7:2" {
		t.Fatalf("every reply's runs: %+v %v", all, err)
	}
}
