package sparenotice

import (
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/usage"
)

func openDB(t *testing.T) db.Q {
	t.Helper()
	d, err := store.Open(filepath.Join(t.TempDir(), "spare.sqlite"))
	if err == nil {
		t.Cleanup(func() { d.Close() })
		return d
	}
	t.Fatal(err)
	return nil
}

type rig struct {
	*Env
	now      time.Time
	forecast usage.Forecast
	waiting  bool
	asked    []string
	told     []string
}

func newRig(t *testing.T) *rig {
	r := &rig{now: time.Date(2026, 10, 27, 12, 0, 0, 0, time.Local)}
	left := 18.0
	r.forecast = usage.Forecast{LeftAtReset: &left, HoursLeft: 14}
	r.Env = &Env{Q: openDB(t), Accounts: func() []string { return []string{"theta"} },
		Forecast: func(string) (usage.Forecast, bool) { return r.forecast, true },
		ResetAt:  func(string) int64 { return time.Date(2026, 10, 28, 2, 10, 0, 0, time.Local).Unix() },
		Waiting:  func(string) bool { return r.waiting },
		Ask: func(title, _ string, tags ...string) (int64, error) {
			r.asked = append(r.asked, title+"|"+strings.Join(tags, ","))
			return 1, nil
		},
		Tell: func(text string) error { r.told = append(r.told, text); return nil },
		Now:  func() time.Time { return r.now }}
	return r
}

func spare(t *testing.T, r *rig, title string, tags ...string) int64 {
	t.Helper()
	tid, err := db.NewTask(r.Q, title, "bob", "boss", 0, db.DefaultPriority, "", 0, "")
	if err != nil {
		t.Fatal(err)
	}
	for _, g := range tags {
		r.Q.Exec("INSERT INTO task_tags(task_id, tag) VALUES(?, ?)", tid, g)
	}
	return tid
}

func TestALossComingNamesTheSpareDraftsToTheFounderAndHandlineOnce(t *testing.T) {
	r := newRig(t)
	a, b := spare(t, r, "audit", SpareTag), spare(t, r, "tests", SpareTag)
	spare(t, r, "not spare")
	if err := r.Sweep(0); err != nil {
		t.Fatal(err)
	}
	want := "18% of theta's weekly use will be lost at 02:10. These spare tasks can use it: " + TaskList([]int64{a, b}) + "."
	if len(r.asked) != 1 || r.asked[0] != want+"|"+NoticeTag || len(r.told) != 1 || r.told[0] != want {
		t.Fatalf("asked %v told %v", r.asked, r.told)
	}
	r.Sweep(0)
	r.now = r.now.Add(11 * time.Hour)
	r.Sweep(0)
	if len(r.asked) != 1 {
		t.Fatalf("looked at no more than every 10 minutes, told once per 12 hours: %v", r.asked)
	}
	left := 37.0
	r.forecast.LeftAtReset = &left
	r.now = r.now.Add(11 * time.Minute)
	r.Sweep(0)
	if len(r.asked) != 2 {
		t.Fatalf("told again when the loss doubled: %v", r.asked)
	}
	r.now = r.now.Add(13 * time.Hour)
	r.Sweep(0)
	if len(r.asked) != 3 {
		t.Fatalf("and after 12 hours: %v", r.asked)
	}
}

func TestNoNoticeUnlessTheLossIsTenPointsWithinTwoDaysAndNothingIsQueuedForTheAccount(t *testing.T) {
	for name, change := range map[string]func(r *rig){
		"a small loss":       func(r *rig) { v := 9.9; r.forecast.LeftAtReset = &v },
		"more than two days": func(r *rig) { r.forecast.HoursLeft = 49 },
		"work is queued":     func(r *rig) { r.waiting = true },
		"no forecast":        func(r *rig) { r.Forecast = func(string) (usage.Forecast, bool) { return usage.Forecast{}, false } },
	} {
		r := newRig(t)
		change(r)
		if err := r.Sweep(0); err != nil || len(r.asked) != 0 || len(r.told) != 0 {
			t.Errorf("%s: %v %v %v", name, err, r.asked, r.told)
		}
	}
}

func TestWithNoSpareTaskTheNoticeSaysSo(t *testing.T) {
	r := newRig(t)
	r.Sweep(0)
	if len(r.asked) != 1 || !strings.Contains(r.asked[0], "No spare task is waiting") {
		t.Fatalf("%v", r.asked)
	}
}
