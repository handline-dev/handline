package prompts

import (
	"context"
	"io"
	"net"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

func asBackend() *rpc.Conn {
	return &rpc.Conn{Caller: rpc.Caller{Role: "agent", Info: map[string]any{"kind": "agent", "name": "backend"}}}
}

type asked struct {
	reply map[string]any
	err   error
}

// ask calls permission.ask as backend's session in the background, and waits until its question is the open prompt.
func ask(t *testing.T, e *Env, command string) (<-chan asked, *Block) {
	t.Helper()
	out := make(chan asked, 1)
	go func() {
		r, err := e.permissionAsk(asBackend(), map[string]any{"tool": "Bash", "input": map[string]any{"command": command, "description": "clean up"}})
		m, _ := r.(map[string]any)
		out <- asked{m, err}
	}()
	return out, waitOpen(t, e, command)
}

func waitOpen(t *testing.T, e *Env, command string) *Block {
	t.Helper()
	for i := 0; i < 200; i++ {
		if b, _ := e.OpenBlockOf("backend"); b != nil && b.Detail["command"] == command {
			return b
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("no open question for %q", command)
	return nil
}

func reply(t *testing.T, ch <-chan asked) map[string]any {
	t.Helper()
	select {
	case a := <-ch:
		if a.err != nil {
			t.Fatal(a.err)
		}
		return a.reply
	case <-time.After(5 * time.Second):
		t.Fatal("the call did not return")
	}
	return nil
}

// The hook's question is a prompt answered allow or deny through its waiting call: the screen watch leaves it alone, nothing is typed, and
// a deny's note reaches the agent.
func TestAHookQuestionIsAnsweredThroughItsCall(t *testing.T) {
	e, h := testEnv(t)
	seedAgent(t, e, "backend", "%1")
	register(e)
	h.set("%1", []string{"> working"})
	call, b := ask(t, e, "rm -rf node_modules")
	if _, err := e.WatchOnce(t.Logf); err != nil {
		t.Fatal(err)
	}
	if again, _ := e.OpenBlockOf("backend"); again == nil || again.ID != b.ID {
		t.Fatal("the screen watch closed a question the hook holds")
	}
	if p := openPrompt(t, e, "backend"); p["source"] != "hook" || p["command"] != "rm -rf node_modules" || p["reason"] != "clean up" {
		t.Fatalf("%v", p)
	}
	refusal(t, mustErr(answers(e, b.Fingerprint, "yes")), "bad_request")
	refusal(t, mustErr(e.answer(asRon(), map[string]any{"agent": "backend", "prompt": b.Fingerprint, "choice": "allow", "note": "x"})), "bad_request")
	if _, err := e.answer(asRon(), map[string]any{"agent": "backend", "prompt": b.Fingerprint, "choice": "deny", "note": " use the script "}); err != nil {
		t.Fatal(err)
	}
	if r := reply(t, call); r["behavior"] != "deny" || r["message"] != "The founder said no: use the script" {
		t.Fatalf("%v", r)
	}
	if left, _ := e.OpenOf("backend"); left != nil {
		t.Fatal("the answered question is still open")
	}
	if typed := h.sent; len(typed) != 0 {
		t.Fatalf("keys were typed: %v", typed)
	}

	call, b = ask(t, e, "make build")
	if _, err := answers(e, b.Fingerprint, "allow"); err != nil {
		t.Fatal(err)
	}
	if r := reply(t, call); r["behavior"] != "allow" || r["message"] != nil {
		t.Fatalf("%v", r)
	}
}

// One question per agent is open at a time: a second waits for the first. A call that ends unanswered closes its question.
func TestASecondQuestionWaitsAndAnEndedCallClosesItsOwn(t *testing.T) {
	e, _ := testEnv(t)
	register(e)
	first, b := ask(t, e, "one")
	ctx, cancel := context.WithCancel(context.Background())
	second := make(chan bool, 1)
	go func() {
		_, answered, _ := e.AskPermission(ctx, "backend", "Bash", map[string]any{"command": "two"})
		second <- answered
	}()
	time.Sleep(50 * time.Millisecond)
	if open, _ := e.OpenOf("backend"); open == nil || open.ID != b.ID {
		t.Fatal("the second question replaced the first")
	}
	if _, err := answers(e, b.Fingerprint, "allow"); err != nil {
		t.Fatal(err)
	}
	reply(t, first)
	waitOpen(t, e, "two")
	cancel()
	if answered := <-second; answered {
		t.Fatal("a cancelled call was answered")
	}
	if open, _ := e.OpenOf("backend"); open != nil {
		t.Fatal("the cancelled call left its question open")
	}
}

// A question whose call is gone without closing it (the daemon restarted) goes stale and the watch closes it.
func TestAStaleHookQuestionIsClosedByTheWatch(t *testing.T) {
	e, h := testEnv(t)
	seedAgent(t, e, "backend", "%1")
	h.set("%1", []string{"> working"})
	old := time.Now().Unix() - 3*StaleS
	if _, err := e.DB.Exec("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, answerable, first_seen, last_seen) "+
		"VALUES('backend', 'permission', 'fp', 't', '[]', '{\"source\": \"hook\"}', 1, ?, ?)", old, old); err != nil {
		t.Fatal(err)
	}
	if _, err := e.WatchOnce(t.Logf); err != nil {
		t.Fatal(err)
	}
	if open, _ := e.OpenOf("backend"); open != nil {
		t.Fatal("a stale hook question stayed open")
	}
	if _, err := e.permissionAsk(asRon(), map[string]any{"tool": "Bash", "input": map[string]any{}}); err == nil {
		t.Fatal("a person asked as a session")
	}
}

// A question closed under its call (the watch found it stale after the machine slept) ends the call with no answer at its next refresh,
// so the agent's own dialog shows rather than nothing for an hour.
func TestAQuestionClosedUnderItsCallEndsTheCallUnanswered(t *testing.T) {
	e, _ := testEnv(t)
	register(e)
	call, b := ask(t, e, "rm -rf build")
	if err := e.closeRow(b.ID, time.Now().Unix()); err != nil {
		t.Fatal(err)
	}
	select {
	case a := <-call:
		if a.err != nil || a.reply["behavior"] != nil {
			t.Fatalf("%v %v", a.reply, a.err)
		}
	case <-time.After(2 * StaleS * time.Second):
		t.Fatal("the call still waits on a question nobody sees")
	}
}

// An agent that stops or takes a new prompt while its question waits has moved on (answered at its own dialog, or ended): the call ends
// unanswered at its next refresh and closes the question, instead of showing a dialog for an hour. Other events do not end it.
func TestAQuestionTheAgentMovedOnFromIsClosed(t *testing.T) {
	for _, c := range []struct {
		kind string
		ends bool
	}{{"Stop", true}, {"UserPromptSubmit", true}, {"Notification", false}, {"PostToolUse", false}} {
		e, _ := testEnv(t)
		register(e)
		call, b := ask(t, e, "cd /x && make")
		wait := 2 * StaleS * time.Second
		if !c.ends {
			wait = 2 * StaleS / 3 * time.Second * 2 // two refreshes
		}
		if _, err := db.AddEvent(e.DB, "backend", c.kind); err != nil {
			t.Fatal(err)
		}
		select {
		case a := <-call:
			if !c.ends || a.err != nil || a.reply["behavior"] != nil {
				t.Fatalf("%s: returned %v %v", c.kind, a.reply, a.err)
			}
			if open, _ := e.OpenOf("backend"); open != nil {
				t.Fatalf("%s: the question stays open", c.kind)
			}
		case <-time.After(wait):
			if c.ends {
				t.Fatalf("%s: the call still waits on a question nobody sees", c.kind)
			}
			if open, _ := e.OpenOf("backend"); open == nil || open.ID != b.ID {
				t.Fatalf("%s: the question was closed", c.kind)
			}
			if _, err := answers(e, b.Fingerprint, "allow"); err != nil {
				t.Fatal(err)
			}
			reply(t, call)
		}
	}
}

type sessionOf struct{ agent string }

func (s sessionOf) AgentOf(int) string       { return s.agent }
func (s sessionOf) SessionsReadable() error  { return nil }
func (s sessionOf) FounderKeyOK(string) bool { return false }

// The hook's client going away (its process killed by the agent) ends the waiting call within a refresh and closes its question.
func TestAQuestionWhoseHookClientIsGoneIsClosed(t *testing.T) {
	e, _ := testEnv(t)
	register(e)
	cl, sv := net.Pipe()
	srv := rpc.NewServer(sessionOf{"backend"}, "abc1234", 7, "local", func(string, ...any) {})
	Register(srv, e)
	served := make(chan struct{})
	go func() { srv.Serve(rpc.NewLineTransport(sv), 0); sv.Close(); close(served) }()
	cl.SetDeadline(time.Now().Add(10 * time.Second))
	go io.Copy(io.Discard, cl) // a pipe holds nothing: the replies are read as they come
	for _, line := range []string{`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t/1","protocol":1}}`,
		`{"jsonrpc":"2.0","id":2,"method":"permission.ask","params":{"tool":"Bash","input":{"command":"cd /x"}}}`} {
		if _, err := cl.Write([]byte(line + "\n")); err != nil {
			t.Fatal(err)
		}
	}
	waitOpen(t, e, "cd /x")
	cl.Close()
	select {
	case <-served:
	case <-time.After(5 * time.Second):
		t.Fatal("the connection was not served out")
	}
	for i := 0; i < 100; i++ {
		if open, _ := e.OpenOf("backend"); open == nil {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal("the question stays open after its client went")
}
