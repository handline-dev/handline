package reads

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

func queueOf(t *testing.T, e *Env, c *rpc.Conn, params map[string]any) map[string]any {
	t.Helper()
	got, err := e.inboxQueue(c, params)
	if err != nil {
		t.Fatal(err)
	}
	return got.(map[string]any)
}

// Python's hl inbox (cmd_inbox in handline/cli/ask.py) on the database the Go method reads, run as the founder.
const pythonInbox = `
import os, sys, types
sys.path.insert(0, os.getcwd())
from handline.cli import ask
from handline.team import ask as askmod
askmod.asking_agent = lambda: "human"
sys.exit(ask.cmd_inbox(types.SimpleNamespace(agent=sys.argv[1], all=sys.argv[2] == "all", wait=False, timeout=0)))
`

func pythonHlInbox(t *testing.T, home, whose string, all bool) string {
	t.Helper()
	root, _ := filepath.Abs("../../..")
	flag := "head"
	if all {
		flag = "all"
	}
	cmd := exec.Command("python3", "-I", "-c", pythonInbox, whose, flag)
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "OCP_HOME="+home, "HANDLINE_HOME="+home, "HANDLINE_TEST_ROOT="+filepath.Dir(home), "OCP_TEST_ROOT="+filepath.Dir(home))
	out, err := cmd.CombinedOutput()
	if errors.Is(err, exec.ErrNotFound) {
		t.Skip("no python3:", err)
	}
	if err != nil {
		t.Fatalf("the Python hl inbox failed: %v: %s", err, out)
	}
	return string(out)
}

// drawn is the reply as cmd_inbox prints it: only layout is added here, every word of a request and every sentence is the daemon's.
func drawn(q map[string]any, all bool) string {
	var b strings.Builder
	if q["whose"] == human {
		lines := q["lines"].([]any)
		if len(lines) == 0 && !all {
			return "nothing waiting on you\n"
		}
		fmt.Fprintf(&b, "%d waiting. `hl read <id>` shows one whole.\n", q["waiting"])
		for _, l := range lines {
			l := l.(map[string]any)
			mark, behind := ".", ""
			if l["decision"].(bool) {
				mark = "!"
			} else if l["answer"].(bool) {
				mark = "<"
			}
			if n := l["unread"].(int); n > 1 {
				behind = fmt.Sprintf(" (+%d)", n-1)
			}
			fmt.Fprintf(&b, "%s #%-5d %-12s %-9s %s%s\n", mark, l["id"], l["from"], l["kind"], l["line"], behind)
		}
		b.WriteString("`hl say <who> --parent <id> \"<text>\"` answers one - a reply settles what it answers, and on a `<` row it goes back in that thread.\n")
		if all {
			dealt := q["dealt"].([]any)
			fmt.Fprintf(&b, "\n%d most recent, already dealt with:\n", len(dealt))
			for _, d := range dealt {
				d := d.(map[string]any)
				arrow := "←"
				if d["outgoing"].(bool) {
					arrow = "→"
				}
				fmt.Fprintf(&b, "  #%-5d %s %-12s %-9s %s\n", d["id"], arrow, d["agent"], d["state"], d["line"])
			}
		}
		return b.String()
	}
	if all {
		rows := q["history"].([]any)
		if len(rows) == 0 {
			return fmt.Sprintf("%s: inbox empty\n", q["whose"])
		}
		fmt.Fprintf(&b, "%d total. `hl read <id>` shows one whole.\n", len(rows))
		for _, r := range rows {
			r := r.(map[string]any)
			fmt.Fprintf(&b, "#%-5d from %-12s %-9s depth=%d %s\n", r["id"], r["requester"], r["state"], r["chain_depth"], r["line"])
		}
		return b.String()
	}
	head, ok := q["head"].(map[string]any)
	if !ok {
		return fmt.Sprintf("%s: inbox empty\n", q["whose"])
	}
	fmt.Fprintf(&b, "#%d from %s  depth=%d\n\n%s\n", head["id"], head["requester"], head["chain_depth"], head["body"])
	if w := head["more_waiting_line"].(string); w != "" {
		fmt.Fprintf(&b, "\n%s\n", w)
	}
	fmt.Fprintf(&b, "\n%s. `hl inbox --all` shows the whole queue and its history.\n", head["how_to_close"])
	return b.String()
}

func sameAsPython(t *testing.T, e *Env, whose string) {
	t.Helper()
	for _, all := range []bool{false, true} {
		got := drawn(queueOf(t, e, asFounder, map[string]any{"whose": whose, "all": all}), all)
		if want := pythonHlInbox(t, e.Home, whose, all); got != want {
			t.Errorf("hl inbox %s (all %v)\nGo:\n%s\nPython:\n%s", whose, all, got, want)
		}
	}
}

func TestInboxQueueOfAnAgentPrintsAsThePythonHlInbox(t *testing.T) {
	e := testEnv(t)
	sameAsPython(t, e, "bob") // empty
	long := strings.Repeat("a long first line ", 6)
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok, chain_depth) VALUES"+
		"(1, 1, 'amy', 'bob', 'old news', 'landed', 'ask', 1, 0), (2, 2, 'amy', 'bob', 'fyi', 'queued', 'say', 1, 0), "+
		"(3, 3, 'amy', 'bob', ?, 'queued', 'ask', 1, 2), (4, 4, 'cy', 'bob', 'T1 handed to you', 'queued', 'task', 1, 0), "+
		"(5, 5, 'cy', 'bob', 'not routed yet', 'queued', 'ask', 0, 0), (6, 6, 'cy', 'bob', 'taken back', 'withdrawn', 'ask', 1, 0)", long+"\nsecond line")
	sameAsPython(t, e, "bob") // an ask at the head, a say and a task behind it
	got := queueOf(t, e, asBob, nil)
	if head := got["head"].(map[string]any); head["id"] != int64(3) || head["more_waiting"] != 3 || got["newest"] != int64(4) {
		t.Fatalf("the head is the oldest queued request that is not a say, and newest the newest still open: %v", got)
	}
	run(t, e, "UPDATE requests SET state = 'landed' WHERE id = 3")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'bob', 'cy', 1)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 4)")
	sameAsPython(t, e, "bob") // a task at the head
	run(t, e, "UPDATE requests SET state = 'landed' WHERE id = 4")
	sameAsPython(t, e, "bob") // only a say left: no head
}

func TestInboxQueueOfTheFounderPrintsAsThePythonHlInbox(t *testing.T) {
	e := testEnv(t)
	sameAsPython(t, e, human) // nothing waiting
	run(t, e, "INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok, response, landed_at) VALUES"+
		"(1, 10, 'amy', 'human', 'seen before', 'landed', 'say', 1, NULL, 10), (2, 20, 'amy', 'human', 'report one', 'landed', 'say', 1, NULL, 20), "+
		"(3, 30, 'amy', 'human', 'report two\nmore', 'landed', 'say', 1, NULL, 30), (4, 40, 'bob', 'human', 'which one?', 'queued', 'ask', 1, 'async', NULL), "+
		"(5, 50, 'bob', 'human', 'approve the plan', 'queued', 'task', 1, NULL, NULL), (6, 60, 'human', 'cy', 'do it', 'landed', 'ask', 1, NULL, 60), "+
		"(7, 70, 'cy', 'human', 'done as asked', 'landed', 'say', 1, NULL, 70), (8, 80, 'human', 'cy', 'and this', 'withdrawn', 'ask', 1, NULL, NULL), "+
		"(9, 90, 'bob', 'human', 'old question', 'landed', 'ask', 1, NULL, 90), (10, 95, 'dee', 'human', 'no kind', 'queued', NULL, 1, NULL, NULL)")
	run(t, e, "UPDATE requests SET settles = 6 WHERE id = 7")
	run(t, e, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'decide', 'human', 'bob', 1)")
	run(t, e, "INSERT INTO task_requests(task_id, request_id) VALUES(1, 5)")
	run(t, e, "INSERT INTO cursors(name, row_id, updated) VALUES('for_you_seen:amy', 1, 1)")
	run(t, e, `INSERT INTO events(ts, agent, kind, payload) VALUES(91, 'human', 'dismissed', '{"request_id": 9, "by": "human", "reason": "stale"}')`)
	sameAsPython(t, e, human)
	got := queueOf(t, e, asFounder, nil)
	if got["waiting"] != 7 || got["newest"] != int64(10) || got["head"] != nil || got["dealt"] != nil {
		t.Fatalf("%v", got)
	}
	first := got["lines"].([]any)[0].(map[string]any)
	if want := map[string]any{"id": int64(5), "from": "bob", "kind": "task", "decision": true, "answer": false, "line": "approve the plan", "unread": 1}; !reflect.DeepEqual(first, want) {
		t.Fatalf("a decision comes first: %v", first)
	}
}

func TestInboxQueueListsOnlyForTheAgentItsSupervisorAndTheFounder(t *testing.T) {
	e := testEnv(t)
	os.WriteFile(filepath.Join(e.Home, "team.yaml"), []byte("agents:\n  bob: {}\n  bob.kid: {}\n  amy: {}\n"), 0o644)
	for whose, ok := range map[string]bool{"bob": true, "bob.kid": true, "amy": false, human: false} {
		_, err := e.inboxQueue(asBob, map[string]any{"whose": whose})
		var refused *rpc.Error
		if ok && err != nil {
			t.Errorf("bob lists %s: %v", whose, err)
		} else if !ok && (!errors.As(err, &refused) || refused.Code != "refused" || refused.Data["gate"] != "listing" ||
			refused.Message != "bob may not list "+whose+"'s messages: only "+whose+", its supervisor and the human may. `hl read <id>` shows one request by number.") {
			t.Errorf("bob lists %s: %v", whose, err)
		}
	}
	if _, err := e.inboxQueue(asFounder, map[string]any{"whose": "amy"}); err != nil {
		t.Fatal(err)
	}
}

func TestInboxQueueWaitsForSomethingNewerThanSince(t *testing.T) {
	e := testEnv(t)
	queue(t, e, 1, "ask")
	began := time.Now()
	if got := queueOf(t, e, asBob, map[string]any{"wait_s": num64(5)}); got["newest"] != int64(1) || time.Since(began) > 2*time.Second {
		t.Fatalf("something waits, so the call does not: %v after %v", got, time.Since(began))
	}
	go func() {
		time.Sleep(300 * time.Millisecond)
		e.DB.Exec("INSERT INTO requests(id, ts, requester, target, body, state, kind, edge_ok) VALUES(2, 2, 'amy', 'bob', 'b', 'queued', 'say', 1)")
	}()
	if got := queueOf(t, e, asBob, map[string]any{"since": num64(1), "wait_s": num64(5)}); got["newest"] != int64(2) || got["head"].(map[string]any)["id"] != int64(1) {
		t.Fatalf("held until a newer one came: %v", got)
	}
	began = time.Now()
	if got := queueOf(t, e, asBob, map[string]any{"since": num64(2), "wait_s": num64(1)}); got["newest"] != int64(2) || time.Since(began) < time.Second {
		t.Fatalf("nothing newer: the inbox as it is, after the wait: %v after %v", got, time.Since(began))
	}
	for _, bad := range []map[string]any{{"wait_s": num64(601)}, {"since": "x"}, {"all": "yes"}, {"whose": num64(1)}} {
		if _, err := e.inboxQueue(asBob, bad); err == nil {
			t.Errorf("%v is refused", bad)
		}
	}
}
