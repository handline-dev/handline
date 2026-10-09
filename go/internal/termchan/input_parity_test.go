package termchan

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// Python's inputhold, run on a copy of the database the Go side works on: the events each leaves must be the same text.
const pythonHold = `
import os, sys
sys.path.insert(0, os.getcwd())
from handline import db
from handline.daemon import inputhold
con = db.connect()
if sys.argv[1] == "read":
    inputhold.holder(con, "ann", int(sys.argv[2]))
else:
    inputhold.take(con, "ann", "person", "robin", int(sys.argv[2]))
con.commit()
`

func pythonHolds(t *testing.T, r rig, how, now string) []string {
	t.Helper()
	root, _ := filepath.Abs("../../..")
	cmd := exec.Command("python3", "-I", "-c", pythonHold, how, now)
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "HANDLINE_HOME="+r.e.Home, "OCP_HOME="+r.e.Home, "HANDLINE_TEST_ROOT="+filepath.Dir(r.e.Home), "OCP_TEST_ROOT="+filepath.Dir(r.e.Home))
	out, err := cmd.CombinedOutput()
	if errors.Is(err, exec.ErrNotFound) {
		t.Skip("no python3:", err)
	}
	if err != nil { // Python ran and failed: the parity is broken, not unavailable
		t.Fatalf("the Python inputhold failed: %v: %s", err, strings.TrimSpace(string(out)))
	}
	return holderPayloads(t, r)
}

func TestTheHolderEventsAreThePythonEvents(t *testing.T) {
	for _, how := range []string{"read", "take"} {
		t.Run(how, func(t *testing.T) {
			goSide, pySide := newRig(t), newRig(t)
			now := time.Now().Unix() // Python's view of a hold reads the real clock, so both sides run on it
			for _, r := range []rig{goSide, pySide} {
				r.e.Now = func() time.Time { return time.Unix(now, 0) }
				r.agent(t)
				holdIdle(t, r)
			}
			if how == "read" {
				goSide.e.holders("ann")
			} else {
				goSide.e.take(person("robin"), map[string]any{"agent": "ann"})
			}
			want := pythonHolds(t, pySide, how, fmt.Sprint(now))
			got := holderPayloads(t, goSide)
			if strings.Join(got, "\n") != strings.Join(want, "\n") || len(got) == 0 {
				t.Fatalf("go %q, python %q", got, want)
			}
		})
	}
}
