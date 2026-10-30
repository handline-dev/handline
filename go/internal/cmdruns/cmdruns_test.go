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
		b := Block{7, n}
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
	b := Block{3, 0}
	Start(q, b, "%1", 100)
	if err := Settle(q, nil, 500); err != nil { // tmux could not answer: nothing ended
		t.Fatal(err)
	}
	if runs, _ := Of(q, 3); len(runs) != 1 || runs[0].State != "running" {
		t.Fatalf("%+v", runs)
	}
	Start(q, b, "%2", 600)
	Start(q, Block{3, 1}, "%3", 550)
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
	Start(q, Block{5, 0}, "%1", 1000)
	Start(q, Block{5, 1}, "%2", 1000)
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
