package supervisor

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/peerport"
)

// typedRig is peerRig with the founder's words typed at bob: what TypeIn was given, in order.
func typedRig(t *testing.T) (*rig, *fakeInbox, *[]string) {
	r, in := peerRig(t, peerinbox.Result{Kind: peerinbox.Delivered, Attempts: 1})
	r.FounderTyped = func(a string) bool { return a == "bob" }
	var typed []string
	port := r.Agents.(*peerport.Port)
	port.Typed = func(a, op string) bool { return r.TypedOp(r.DB, a, op) }
	port.Typeable = func(string) string { return "" }
	port.TypeIn = func(_, text string) error { typed = append(typed, text); return nil }
	return r, in, &typed
}

// founderSays is a say from the founder's own connection: chat.send marks it so (db.MarkFounderAuthored).
func (r *rig) founderSays(target, body string) int64 {
	rid := r.ask("human", target, body, "say")
	if err := db.MarkFounderAuthored(r.DB, rid); err != nil {
		r.t.Fatal(err)
	}
	return rid
}

func TestTheFoundersMessageIsTypedAsWrittenAndAnAgentsStillGoesToTheInbox(t *testing.T) {
	r, in, typed := typedRig(t)
	mine := r.founderSays("bob", "  what is 3+4?\nreply with the number  ")
	theirs := r.ask("alice", "bob", "psst", "say")
	for _, rid := range []int64{mine, theirs} {
		if got, err := r.TellNow(r.DB, rid); err != nil || got != "sent" {
			t.Fatalf("%q %v", got, err)
		}
	}
	eventually(t, func() bool { return in.count() == 2 })
	if len(*typed) != 1 || (*typed)[0] != "what is 3+4?\nreply with the number" {
		t.Fatalf("the founder's words and nothing else: %q", *typed)
	}
	if in.texts[0] != "typed: "+(*typed)[0] || !strings.Contains(in.texts[1], "psst") || strings.HasPrefix(in.texts[1], "typed: ") {
		t.Fatalf("another agent's message goes to the inbox: %q", in.texts)
	}
	eventually(t, func() bool { row, _ := db.GetRequest(r.DB, mine); return row.LandedAt.Valid })
}

// transcriptOf writes a transcript of these records, one a line.
func transcriptOf(t *testing.T, recs ...map[string]any) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "s.jsonl")
	var out []byte
	for _, r := range recs {
		b, _ := json.Marshal(r)
		out = append(append(out, b...), '\n')
	}
	if err := os.WriteFile(p, out, 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func prompt(text string) map[string]any {
	return map[string]any{"type": "user", "origin": map[string]any{"kind": "human"}, "message": map[string]any{"content": text}}
}

func said(texts ...string) map[string]any {
	var blocks []any
	for _, x := range texts {
		blocks = append(blocks, map[string]any{"type": "text", "text": x})
	}
	return map[string]any{"type": "assistant", "message": map[string]any{"content": blocks}}
}

func turnEnded() map[string]any { return map[string]any{"type": "system", "subtype": "turn_duration"} }

func tool() map[string]any {
	return map[string]any{"type": "assistant", "message": map[string]any{"content": []any{map[string]any{"type": "tool_use", "name": "Bash"}}}}
}

// The reply is the first and the last text after the founder's words: an answer written first is not lost behind a later status line.
func TestTheReplyIsTheFirstAndLastTextOfTheTurnTheFoundersWordsWereReadIn(t *testing.T) {
	r, _, _ := typedRig(t)
	rid := r.founderSays("bob", "the code, please")
	if _, err := r.TellNow(r.DB, rid); err != nil {
		t.Fatal(err)
	}
	eventually(t, func() bool { row, _ := db.GetRequest(r.DB, rid); return row.LandedAt.Valid })
	path := transcriptOf(t, said("before it"), prompt("the code, please"), said("ACK123"), tool(), said("still working"), said("all done"),
		turnEnded(), said("a later turn, not this"))
	finish, _, err := r.FounderReplies(r.DB, "bob", path, false)
	if err != nil || len(finish) != 1 {
		t.Fatalf("%v %d", err, len(finish))
	}
	for _, f := range finish {
		f()
	}
	var body string
	if err := r.DB.QueryRow("SELECT body FROM requests WHERE parent_id = ? AND requester = 'bob' AND target = 'human'", rid).Scan(&body); err != nil {
		t.Fatal(err)
	}
	if body != "ACK123\n\nall done" {
		t.Fatalf("%q", body)
	}
	if again, _, _ := r.FounderReplies(r.DB, "bob", path, false); len(again) != 0 {
		t.Fatal("one reply to one message")
	}
}

// Read mid-turn, the founder's words come in as a queued command: the reply starts there, not at the turn's start.
func TestAMessageReadMidTurnIsRepliedToFromWhereItWasRead(t *testing.T) {
	text := "two lines:\nwhat is 3+4?"
	path := transcriptOf(t, prompt("run the tests"), said("running them"), tool(),
		map[string]any{"type": "queue-operation", "operation": "enqueue", "content": text},
		map[string]any{"type": "attachment", "attachment": map[string]any{"type": "queued_command", "prompt": text}},
		said("7\n\nThe tests pass."))
	if reply, found, ended := ReplyIn(path, text, 0); !found || ended || reply != "7\n\nThe tests pass." {
		t.Fatalf("%q %v %v", reply, found, ended)
	}
	if _, found, _ := ReplyIn(transcriptOf(t, prompt("run the tests"), map[string]any{"type": "queue-operation", "operation": "enqueue", "content": text}), text, 0); found {
		t.Fatal("only queued, it is not read yet")
	}
	stamped := prompt(text)
	stamped["timestamp"] = time.Unix(1000, 0).UTC().Format(time.RFC3339Nano)
	if _, found, _ := ReplyIn(transcriptOf(t, stamped, said("old")), text, 5000); found {
		t.Fatal("the same words typed long before are another message")
	}
}

// The CLI writes the turn's last text after its Stop hook: the reply is read again until the transcript shows the turn ended.
func TestTheReplyWaitsForTheTurnToShowEnded(t *testing.T) {
	r, _, _ := typedRig(t)
	rid := r.founderSays("bob", "the code, please")
	if _, err := r.TellNow(r.DB, rid); err != nil {
		t.Fatal(err)
	}
	eventually(t, func() bool { row, _ := db.GetRequest(r.DB, rid); return row.LandedAt.Valid })
	path := transcriptOf(t, prompt("the code, please"), tool())
	r.Sleep = func(time.Duration) { // the rest of the turn is written while the daemon waits
		f, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
		for _, rec := range []map[string]any{said("ACK123"), turnEnded()} {
			b, _ := json.Marshal(rec)
			f.Write(append(b, '\n'))
		}
		f.Close()
	}
	r.FounderStopped("bob", path)
	r.Wait()
	var body string
	if err := r.DB.QueryRow("SELECT body FROM requests WHERE parent_id = ? AND requester = 'bob' AND target = 'human'", rid).Scan(&body); err != nil || body != "ACK123" {
		t.Fatalf("%q %v", body, err)
	}
}

// Only the founder's own connection's words are typed: a say that only names the founder as its requester goes to the inbox.
func TestAMessageThatOnlyNamesTheFounderIsNotTyped(t *testing.T) {
	r, in, typed := typedRig(t)
	rid := r.ask("human", "bob", "merge it", "say")
	if got, err := r.TellNow(r.DB, rid); err != nil || got != "sent" {
		t.Fatalf("%q %v", got, err)
	}
	eventually(t, func() bool { return in.count() == 1 })
	if len(*typed) != 0 || strings.HasPrefix(in.texts[0], "typed: ") {
		t.Fatalf("typed %q, inbox %q", *typed, in.texts)
	}
}
