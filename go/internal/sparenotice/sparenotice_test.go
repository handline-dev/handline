package sparenotice

import (
	"errors"
	"fmt"
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
	closed   []string
	closeErr error
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
			return db.NewTask(r.Q, title, db.Human, db.Human, 0, db.DefaultPriority, "", 0, "")
		},
		Close: func(tid int64, words string) error {
			if r.closeErr != nil {
				return r.closeErr
			}
			r.closed = append(r.closed, fmt.Sprintf("T%d %s", tid, words))
			_, err := db.MarkTaskDone(r.Q, tid, words, "reason")
			return err
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

// A notice of a loss at a time is wrong once the time has passed: the sweep closes it with the words that it was lost, once.
func TestANoticeIsClosedWhenItsLossTimePasses(t *testing.T) {
	r := newRig(t)
	r.Sweep(0)
	r.forecast.LeftAtReset = nil // nothing new is told as the days pass
	r.now = time.Date(2026, 10, 28, 2, 9, 59, 0, time.Local)
	r.Sweep(0)
	if len(r.closed) != 0 {
		t.Fatalf("closed before its time: %v", r.closed)
	}
	r.now = time.Date(2026, 10, 28, 2, 10, 0, 0, time.Local)
	if err := r.Sweep(0); err != nil {
		t.Fatal(err)
	}
	want := "T1 Its time passed unanswered. 18% of theta's weekly use was lost at 02:10."
	if len(r.closed) != 1 || r.closed[0] != want {
		t.Fatalf("closed %v, want %q", r.closed, want)
	}
	r.now = r.now.Add(time.Hour)
	r.Sweep(0)
	if len(r.closed) != 1 {
		t.Fatalf("closed again: %v", r.closed)
	}
}

func TestANoticeTheFounderAnsweredIsNotClosedAgain(t *testing.T) {
	r := newRig(t)
	r.Sweep(0)
	if _, err := db.MarkTaskDone(r.Q, 1, "dismiss", "reason"); err != nil {
		t.Fatal(err)
	}
	r.now = time.Date(2026, 10, 28, 3, 0, 0, 0, time.Local)
	r.forecast.LeftAtReset = nil
	if err := r.Sweep(0); err != nil || len(r.closed) != 0 {
		t.Fatalf("%v %v", err, r.closed)
	}
	if _, ok, _ := db.Meta(r.Q, pendingKey(1)); ok {
		t.Fatal("the answered notice is still kept")
	}
}

func TestANoticeThatCouldNotBeClosedIsTriedAgainAtTheNextSweep(t *testing.T) {
	r := newRig(t)
	r.Sweep(0)
	r.now = time.Date(2026, 10, 28, 3, 0, 0, 0, time.Local)
	r.forecast.LeftAtReset = nil
	r.closeErr = errors.New("busy")
	if err := r.Sweep(0); err == nil {
		t.Fatal("the failure was dropped")
	}
	r.closeErr = nil
	if err := r.Sweep(0); err != nil || len(r.closed) != 1 {
		t.Fatalf("%v %v", err, r.closed)
	}
}
