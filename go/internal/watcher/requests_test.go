package watcher

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/reads"
)

func TestARequestThatAppearedOrChangedIsToldAsItsRow(t *testing.T) {
	d, l := rig(t)
	w := &Requests{Reads: &reads.Env{DB: d}}
	told := func() []string {
		if err := w.Scan(d, l.Append); err != nil {
			t.Fatal(err)
		}
		got, err := l.Since(0, []string{"requests"}, "", 100)
		if err != nil {
			t.Fatal(err)
		}
		var out []string
		for _, e := range got {
			out = append(out, e.Type+" "+string(e.Data))
		}
		return out
	}
	now := time.Now().Unix()
	exec(t, d, fmt.Sprintf("INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok) VALUES(1, 'aaaaaaaaaaa1', %d, 'human', 'lead', 'hello', 'landed', 'say', 1)", now-10))
	if n := told(); len(n) != 0 {
		t.Fatalf("a first look told %v", n)
	}
	exec(t, d, "UPDATE requests SET no_work = 'thanks' WHERE id = 1")
	got := told()
	if len(got) != 1 || !strings.HasPrefix(got[0], "request.changed ") || !strings.Contains(got[0], `"status":"no_work"`) {
		t.Fatalf("%v", got)
	}
	if n := told(); len(n) != 1 {
		t.Fatalf("no change told again: %v", n)
	}
}
