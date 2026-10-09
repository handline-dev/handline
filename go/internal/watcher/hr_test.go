package watcher

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/stream"
)

func putProposal(t *testing.T, home, id, state string, created, at int64, extra string) {
	t.Helper()
	body := fmt.Sprintf("id: \"%s\"\nkind: retire\nagent: old\nrequested_by: boss\nwhy: \"idle\"\ncreated: %d\nstate: %s\n%s", id, created, state, extra)
	if at != 0 {
		body += fmt.Sprintf("applied: %d\n", at)
	}
	if err := os.MkdirAll(filepath.Join(home, "hr"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, "hr", id+".yaml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func hrRows(t *testing.T, l *stream.Log) []*stream.Event {
	t.Helper()
	got, err := l.Since(0, []string{"hr"}, "", 100)
	if err != nil {
		t.Fatal(err)
	}
	return got
}

func TestAProposalRaisedAndSettledIsToldOnceAndAFirstLookSaysNothing(t *testing.T) {
	d, l := rig(t)
	home := t.TempDir()
	putProposal(t, home, "0000000a", "proposed", 100, 0, "")
	h := &HR{Reads: &reads.Env{DB: d, Home: home}}
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	if err := h.Scan(d, emit); err != nil || len(hrRows(t, l)) != 0 {
		t.Fatalf("the first look said something: %v %v", err, hrRows(t, l))
	}
	if err := h.Scan(d, emit); err != nil || len(hrRows(t, l)) != 0 {
		t.Fatal("nothing changed")
	}
	putProposal(t, home, "0000000b", "proposed", 110, 0, "")
	h.Scan(d, emit)
	got := hrRows(t, l)
	var data map[string]map[string]any
	if len(got) != 1 || got[0].Type != "hr.raised" || *got[0].Agent != "old" || string(got[0].Target) != `{"id":"11","screen":"hr"}` ||
		json.Unmarshal(got[0].Data, &data) != nil || data["proposal"]["id"] != float64(11) || data["proposal"]["state"] != "open" {
		t.Fatalf("%+v %s", got, got[0].Data)
	}
	putProposal(t, home, "0000000a", "rejected", 100, 120, "")
	h.Scan(d, emit)
	if got = hrRows(t, l); len(got) != 2 || got[1].Type != "hr.settled" || *got[1].Title == "" {
		t.Fatalf("%+v", got)
	}
	putProposal(t, home, "0000000b", "approved", 110, 130, "error: no room\n") // decided, but the apply failed
	h.Scan(d, emit)
	putProposal(t, home, "0000000b", "approved", 110, 140, "error: still no room\n") // the apply ran again and failed again
	h.Scan(d, emit)
	if got = hrRows(t, l); len(got) != 4 || got[3].Type != "hr.settled" {
		t.Fatalf("%d rows: %+v", len(got), got)
	}
	h.Scan(d, emit)
	if len(hrRows(t, l)) != 4 {
		t.Fatal("repeated")
	}
}

func TestARestartsFirstLookIsSilentAboutWhatIsInTheStore(t *testing.T) {
	d, l := rig(t)
	home := t.TempDir()
	putProposal(t, home, "0000000a", "proposed", 100, 0, "")
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	(&HR{Reads: &reads.Env{DB: d, Home: home}}).Scan(d, emit)
	second := &HR{Reads: &reads.Env{DB: d, Home: home}} // the daemon started again
	if err := second.Scan(d, emit); err != nil || len(hrRows(t, l)) != 0 {
		t.Fatalf("a restart repeated what was in the store: %v", hrRows(t, l))
	}
}

func TestAFileOnlyChangeWakesTheLoop(t *testing.T) {
	d, l := rig(t)
	home := t.TempDir()
	putProposal(t, home, "0000000a", "proposed", 100, 0, "")
	w := &Watcher{DB: d, Emit: func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }, Log: func(string, ...any) {},
		Detectors: []Detector{&HR{Reads: &reads.Env{DB: d, Home: home}}}, Signature: HRSignature(home)}
	stop := make(chan struct{})
	done := make(chan struct{})
	go func() { w.Run(stop); close(done) }()
	time.Sleep(3 * Every)                                    // the first look
	putProposal(t, home, "0000000b", "proposed", 110, 0, "") // no commit to the database at all
	for i := 0; i < 100 && len(hrRows(t, l)) == 0; i++ {
		time.Sleep(20 * time.Millisecond)
	}
	close(stop)
	<-done
	if got := hrRows(t, l); len(got) != 1 || got[0].Type != "hr.raised" {
		t.Fatalf("%+v", got)
	}
}

func TestGoAndPythonWriteTheSameHrEvents(t *testing.T) {
	home := t.TempDir()
	parityHome = home
	t.Cleanup(func() { parityHome = "" })
	now := time.Now().Unix()
	decided := func(id, state string, created, at int64, extra string) {
		putProposal(t, home, id, state, created, 0, fmt.Sprintf("decided: %d\n%s", at, extra))
	}
	py, events := parity(t, "_hr", func(d *sql.DB) Detector { return &HR{Reads: &reads.Env{DB: d, Home: home}} },
		func(func(string, ...any)) {
			putProposal(t, home, "0000000a", "proposed", now-100, 0, "")
			putProposal(t, home, "0000000c", "proposed", now-90, 0, "")
		},
		func(func(string, ...any)) {
			putProposal(t, home, "0000000b", "proposed", now-10, 0, "")
			decided("0000000a", "rejected", now-100, now-5, "")
			decided("0000000c", "approved", now-90, now-4, "error: no room\n")
			putProposal(t, home, "0000000d", "proposed", now-3, 0, "requested_by: human\n") // the founder's own: never listed
			// one of each kind, raised: the diff and what is asked are the same words as Python's
			for i, f := range []string{
				"kind: add\nagent: new\nteam: eng\nmodel: opus\nvendor: codex\n",
				"kind: settings\nagent: w\nset.model: opus\nset.effort: high\n",
				"kind: link\nagent: a\nto: b\nrelation: peer\n",
				"kind: unlink\nagent: a\nto: b\n",
				"kind: team\nagent: eng\nrepo: r\nmembers: \"a,b\"\n",
				"kind: context\nagent: w\nset.ROLE.md: \"\\\"x\\\"\"\n",
				"kind: rename\nagent: a\nto: b\n",
				"kind: bundle\nagent: x\ni1.kind: retire\ni1.agent: old\ni2.kind: add\ni2.agent: new\n",
			} {
				id := fmt.Sprintf("000000%02x", 0x20+i)
				putProposal(t, home, id, "proposed", now-int64(i), 0, f)
			}
		})
	if len(py) != len(events) || len(py) != 11 {
		t.Fatalf("python wrote %d rows, go %d", len(py), len(events))
	}
	for i, e := range events {
		sameRow(t, i, py[i], e)
		if !same(py[i]["data"], e.Data) {
			t.Errorf("row %d data: python %v, go %s", i, py[i]["data"], e.Data)
		}
	}
}
