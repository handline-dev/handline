package watcher

import (
	"strings"
	"testing"
)

func TestAChangeToAPersonsPrefsIsToldWithTheWholePrefs(t *testing.T) {
	d, l := rig(t)
	w := &Prefs{}
	emit := func() int {
		if err := w.Scan(d, l.Append); err != nil {
			t.Fatal(err)
		}
		got, err := l.Since(0, []string{"prefs"}, "", 100)
		if err != nil {
			t.Fatal(err)
		}
		return len(got)
	}
	exec(t, d, `INSERT INTO prefs(person, nav, updated_at) VALUES('human', '{"pins":["a"],"layout":"flat"}', 1)`)
	if n := emit(); n != 0 {
		t.Fatalf("a first look told %d", n)
	}
	exec(t, d, `UPDATE prefs SET nav = '{"pins":["b"],"layout":"flat"}', updated_at = 2`)
	if n := emit(); n != 1 {
		t.Fatalf("one change told %d", n)
	}
	if n := emit(); n != 1 {
		t.Fatalf("no change told again: %d", n)
	}
	got, _ := l.Since(0, []string{"prefs"}, "", 1)
	if got[0].Type != "prefs.changed" || !strings.Contains(string(got[0].Data), `"pins":["b"]`) || !strings.Contains(string(got[0].Data), `"hidden":[]`) {
		t.Fatalf("%s %s", got[0].Type, got[0].Data)
	}
}
