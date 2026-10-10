package schedules

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/store"
)

type rig struct {
	t     *testing.T
	q     db.Q
	r     *Runner
	items []reads.Schedule
	said  []string
}

func newRig(t *testing.T) *rig {
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	g := &rig{t: t, q: d, items: []reads.Schedule{{Cron: "* * * * *", Message: "check the queue"}}}
	g.r = &Runner{Q: d, Agents: func() []string { return []string{"bob"} }, Schedules: func(string) []reads.Schedule { return g.items },
		Log: func(string) {},
		Say: func(requester, target, body string, recorded func(int64)) error {
			rid, err := db.AddRequest(d, db.NewRequest{Requester: requester, Target: target, Body: body, Response: "fire_and_forget", Kind: "say"})
			if err != nil {
				return err
			}
			g.said = append(g.said, body)
			recorded(rid)
			return nil
		}}
	return g
}

const t0 = int64(1_800_000_000) - 1_800_000_000%60

func (g *rig) sweep(now int64) {
	g.t.Helper()
	if err := g.r.Sweep(now); err != nil {
		g.t.Fatal(err)
	}
}

func TestAScheduleSeenForTheFirstTimeCountsFromNowAndNothingIsSent(t *testing.T) {
	g := newRig(t)
	g.sweep(t0 + 5)
	if len(g.said) != 0 {
		t.Fatalf("%q", g.said)
	}
	if c, _ := db.Cursor(g.q, "schedule_at:bob:"+reads.ScheduleSID(g.items[0])); c != t0 {
		t.Fatalf("the cursor is the minute it was first seen: %d", c)
	}
	g.sweep(t0 + 30)
	if len(g.said) != 0 {
		t.Fatal("not due within the same minute")
	}
}

func TestADueScheduleIsTypedOnceAndSeveralMissedTimesAreOneMessage(t *testing.T) {
	g := newRig(t)
	g.sweep(t0)
	g.sweep(t0 + 600) // ten minutes of daemon down
	if len(g.said) != 1 || g.said[0] != "[schedule * * * * *] check the queue" {
		t.Fatalf("%q", g.said)
	}
	rows, _ := g.q.Query("SELECT payload FROM events WHERE kind = 'schedule_fired' AND agent = 'bob'")
	n := 0
	for rows.Next() {
		var p string
		rows.Scan(&p)
		if !strings.Contains(p, "request_id") {
			t.Fatal(p)
		}
		n++
	}
	rows.Close()
	if n != 1 {
		t.Fatalf("one schedule_fired event, %d", n)
	}
}

func TestAMessageStillUnreadIsNotFollowedByAnotherButItsTimesAreAbsorbed(t *testing.T) {
	g := newRig(t)
	g.sweep(t0)
	g.sweep(t0 + 60)
	g.sweep(t0 + 180) // the first is queued and undelivered: these times are that one
	g.sweep(t0 + 240)
	if len(g.said) != 1 {
		t.Fatalf("%q", g.said)
	}
	// it is read; the next due time sends again
	rid, _ := db.Cursor(g.q, "schedule_rid:bob:"+reads.ScheduleSID(g.items[0]))
	g.q.Exec("UPDATE requests SET delivered_at = 1 WHERE id = ?", rid)
	g.sweep(t0 + 300)
	if len(g.said) != 2 {
		t.Fatalf("%q", g.said)
	}
}

func TestNothingIsSentWhileTheAgentIsMidTurnAndItStaysDueUntilItStops(t *testing.T) {
	g := newRig(t)
	g.sweep(t0)
	db.SetAgentState(g.q, "bob", states.Running, 0, "working")
	g.sweep(t0 + 120)
	g.sweep(t0 + 240)
	if len(g.said) != 0 {
		t.Fatalf("%q", g.said)
	}
	db.SetAgentState(g.q, "bob", states.Idle, 0, "done")
	g.sweep(t0 + 300)
	if len(g.said) != 1 {
		t.Fatalf("%q", g.said)
	}
}

func TestTheCursorsOfAScheduleTheAgentNoLongerHasAreForgotten(t *testing.T) {
	g := newRig(t)
	g.sweep(t0)
	g.sweep(t0 + 120)
	sid := reads.ScheduleSID(g.items[0])
	g.items = nil
	g.sweep(t0 + 240)
	for _, k := range []string{"schedule_at:bob:" + sid, "schedule_rid:bob:" + sid} {
		if c, _ := db.Cursor(g.q, k); c != 0 {
			t.Fatalf("%s kept", k)
		}
	}
	g.items = []reads.Schedule{{Cron: "* * * * *", Message: "check the queue"}}
	g.sweep(t0 + 360)
	if len(g.said) != 1 {
		t.Fatalf("added back later it is new, so it counts from now: %q", g.said)
	}
}
