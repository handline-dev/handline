package compose

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/team"
)

// danglingRig: a session file for this test's own process under account a1, as a session running in a pane would leave.
func danglingRig(t *testing.T) (danglingPorts, *fakeTerminals) {
	t.Helper()
	ft := newFakeTerminals()
	p, _, _, wt := wiredWith(t, ft)
	config, err := team.AccountConfigDir(p.home, "a1")
	if err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(config, "sessions")
	os.MkdirAll(dir, 0o755)
	pid := os.Getpid()
	rec := fmt.Sprintf(`{"pid":%d,"sessionId":"s1","cwd":%q,"version":"2.1.296","peerProtocol":1,"messagingSocketPath":"/tmp/x.sock","status":"idle","updatedAt":1}`, pid, wt)
	os.WriteFile(filepath.Join(dir, fmt.Sprintf("%d.json", pid)), []byte(rec), 0o644)
	os.WriteFile(filepath.Join(dir, fmt.Sprintf("%d.k.key", pid)), []byte("secret\n"), 0o600)
	return danglingPorts{p: p}, ft
}

func TestThePaneSessionIsTheLiveSessionFileWhoseProcessRunsUnderThePane(t *testing.T) {
	d, ft := danglingRig(t)
	ft.panePid = map[string]int{"%1": os.Getppid(), "%2": 1}
	if pid, known := d.PaneSession("%1"); pid != os.Getpid() || !known {
		t.Fatalf("the session under the pane's process: %d %v", pid, known)
	}
	if pid, known := d.PaneSession("%2"); pid != 0 || !known {
		t.Fatalf("a pane with no session under it: %d %v", pid, known)
	}
	if _, known := d.PaneSession("%3"); known {
		t.Fatal("a pane whose process cannot be read is no answer")
	}
}

func TestTheAskGoesThroughThePeerInboxToTheSessionOfThatPid(t *testing.T) {
	d, _ := danglingRig(t)
	var to []int
	result := peerinbox.Result{Kind: peerinbox.Delivered}
	d.deliver = func(_ context.Context, sess peerinbox.Session, _ string, from peerinbox.From) peerinbox.Result {
		to = append(to, sess.PID)
		if from.Name != "daemon" {
			t.Fatalf("the ask is from the daemon: %q", from.Name)
		}
		return result
	}
	if err := d.Ask(os.Getpid(), "write your handoff"); err != nil || len(to) != 1 || to[0] != os.Getpid() {
		t.Fatalf("delivered to the pid's session: %v %v", err, to)
	}
	result = peerinbox.Result{Kind: peerinbox.NotDelivered, Why: "no socket"}
	if err := d.Ask(os.Getpid(), "write your handoff"); err == nil || err.Error() != "no socket" {
		t.Fatalf("an ask not delivered is an error: %v", err)
	}
	if err := d.Ask(999999, "write your handoff"); !errors.Is(err, peerinbox.ErrNoSession) || len(to) != 2 {
		t.Fatalf("a pid with no session file reaches nobody: %v %v", err, to)
	}
}
