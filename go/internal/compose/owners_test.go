package compose

import (
	"os"
	"path/filepath"
	"testing"

	dbpkg "github.com/handline/handline/go/internal/db"
)

// ownersRig is wired(), with a terminal agent (bob) beside the structured one (alice) and an agent_view row for an agent that is gone.
func ownersRig(t *testing.T) (p *Parts, bobRow string, count func(table string) int) {
	t.Helper()
	p, _, _, _ = wired(t)
	d := p.Sink.DB
	bobRow = `{"name":"bob","state":"idle","python":"wrote this"}`
	for agent, view := range map[string]string{"bob": bobRow, "ghost": `{"name":"ghost"}`} {
		if _, err := d.Exec("INSERT INTO agent_view(agent, view, updated) VALUES(?, ?, 7)", agent, view); err != nil {
			t.Fatal(err)
		}
	}
	os.MkdirAll(filepath.Join(p.home, "agents", "bob"), 0o755)
	count = func(table string) int {
		var n int
		if err := d.QueryRow("SELECT COUNT(*) FROM " + table).Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}
	return p, bobRow, count
}

func storedView(t *testing.T, d dbpkg.Q, agent string) (view string, found bool) {
	t.Helper()
	err := d.QueryRow("SELECT view FROM agent_view WHERE agent = ?", agent).Scan(&view)
	return view, err == nil
}

func TestWhilePythonTicksTheGoTickWritesOnlyTheViewsOfStructuredAgentsAndLeavesTheUsageTablesAlone(t *testing.T) {
	p, bobRow, count := ownersRig(t)
	d := p.Sink.DB
	samples := count("usage_samples")
	tk := p.Tick(p.Run.cfg, TickConfig{})
	for i := 0; i < 2; i++ {
		if _, err := tk.Tick.Run(nil); err != nil {
			t.Fatal(err)
		}
	}
	if v, ok := storedView(t, d, "bob"); !ok || v != bobRow {
		t.Fatalf("a terminal agent's view was touched: %q %v", v, ok)
	}
	if _, ok := storedView(t, d, "ghost"); !ok {
		t.Fatal("a row nobody here wrote was dropped")
	}
	if _, ok := storedView(t, d, "alice"); !ok {
		t.Fatal("a structured agent has no view")
	}
	if got := count("usage_samples"); got != samples {
		t.Fatalf("the usage poll is Python's until the cutover: %d samples became %d", samples, got)
	}
}

func TestAtTheCutoverTheGoTickOwnsTheUsagePollAndDropsTheRowOfAGoneAgent(t *testing.T) {
	p, bobRow, count := ownersRig(t)
	d := p.Sink.DB
	samples := count("usage_samples")
	tk := p.Tick(p.Run.cfg, TickConfig{Cutover: true})
	if _, err := tk.Tick.Run(nil); err != nil {
		t.Fatal(err)
	}
	if _, ok := storedView(t, d, "ghost"); ok {
		t.Fatal("the row of a gone agent stays")
	}
	if v, ok := storedView(t, d, "bob"); !ok || v != bobRow {
		t.Fatalf("a registered terminal agent's row is not the Go side's to drop or rewrite: %q %v", v, ok)
	}
	if got := count("usage_samples"); got <= samples {
		t.Fatalf("after the cutover this tick polls usage: %d samples, was %d", got, samples)
	}
}
