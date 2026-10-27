package supervisor

import (
	"strings"
	"testing"

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

// A reply carries the message it answers, as the app shows it: typed alone, "OK merge" says nothing about what it agrees to.
func TestTheFoundersReplyIsTypedWithTheMessageItAnswers(t *testing.T) {
	r, _, typed := typedRig(t)
	parent := r.ask("bob", "human", "#566 merge please\nthe checks are green", "say")
	out, err := r.Ask(r.DB, AskIn{Target: "bob", Body: "OK merge", Requester: "human", Kind: "say", ParentID: parent})
	r.must(err)
	out.Finish()
	r.must(db.MarkFounderAuthored(r.DB, out.RID))
	if got, err := r.TellNow(r.DB, out.RID); err != nil || got != "sent" {
		t.Fatalf("%q %v", got, err)
	}
	eventually(t, func() bool { return len(*typed) == 1 })
	if want := "(in reply to " + db.MsgName(r.DB, parent) + ": #566 merge please)\nOK merge"; (*typed)[0] != want {
		t.Fatalf("typed %q, want %q", (*typed)[0], want)
	}
	eventually(t, func() bool { row, _ := db.GetRequest(r.DB, out.RID); return row.LandedAt.Valid })
}

// A message that starts with ! is typed exactly as written, even as a reply: the reference line would push the ! off the front.
func TestABangMessageIsTypedAsWrittenEvenAsAReply(t *testing.T) {
	r, _, typed := typedRig(t)
	parent := r.ask("bob", "human", "#566 merge please", "say")
	out, err := r.Ask(r.DB, AskIn{Target: "bob", Body: "!git status", Requester: "human", Kind: "say", ParentID: parent})
	r.must(err)
	out.Finish()
	r.must(db.MarkFounderAuthored(r.DB, out.RID))
	if got, err := r.TellNow(r.DB, out.RID); err != nil || got != "sent" {
		t.Fatalf("%q %v", got, err)
	}
	eventually(t, func() bool { return len(*typed) == 1 })
	if (*typed)[0] != "!git status" {
		t.Fatalf("typed %q", (*typed)[0])
	}
}

// A founder's message to an agent that cannot be typed at now is said through the inbox, as before, inside the send's own transaction.
func TestTheFoundersMessageIsSaidThroughTheInboxWhenTheAgentCannotBeTypedAt(t *testing.T) {
	r, in, typed := typedRig(t)
	r.Typeable = func(string) string { return "a shell is left in place of its session" }
	r.Agents.(*peerport.Port).Typeable = r.Typeable
	mine := r.founderSays("bob", "what is 3+4?")
	tx, err := r.DB.Begin()
	if err != nil {
		t.Fatal(err)
	}
	if got, err := r.TellNow(tx, mine); err != nil || got != "sent" {
		t.Fatalf("%q %v", got, err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	eventually(t, func() bool { return in.count() == 1 })
	if len(*typed) != 0 || strings.HasPrefix(in.texts[0], "typed: ") || !strings.Contains(in.texts[0], "what is 3+4?") {
		t.Fatalf("said, not typed: %q %q", *typed, in.texts)
	}
	if typedNow, _ := db.FounderAuthored(r.DB, mine); typedNow {
		t.Fatal("the row still says it was typed, so its landing and reply would be looked for as typed")
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
