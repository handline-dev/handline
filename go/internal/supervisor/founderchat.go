package supervisor

import (
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/textsafe"
)

// The founder's own words, natively (FounderTyped): a message the founder sends an agent is typed into its terminal as its person's prompt,
// with no envelope and no instructions. The agent answers with hl say: its terminal text never reaches the chat. Everything else still
// reaches the agent as a peer's message, so a typed prompt is always the founder's.

// typedRow is whether row is a founder's message to an agent that takes them typed: one the founder's own connection sent
// (db.FounderAuthored), never one that only names the founder as its requester.
func (e *Env) typedRow(q db.Q, row *db.Request) bool {
	return e.FounderTyped != nil && row.Requester == db.Human && row.Kind.String == sayKind && row.Target != db.Human && e.FounderTyped(row.Target) &&
		must(db.FounderAuthored(q, row.ID))
}

func (e *Env) typeable(agent string) string {
	if e.Typeable == nil {
		return ""
	}
	return e.Typeable(agent)
}

// TypedOp is whether the message op to agent is typed, not sent to its inbox: an op that tells it one typed row.
func (e *Env) TypedOp(q db.Q, agent, op string) bool {
	rids := ridsOfOp(op)
	if len(rids) != 1 || e.sayingNow(rids[0]) {
		return false
	}
	row, err := db.GetRequest(q, rids[0])
	return err == nil && row != nil && row.Target == agent && e.typedRow(q, row)
}

// sayInstead marks the founder's message rid to be said through the inbox, as the agent cannot be typed at now. The send in hand reads
// the mark before it is committed, so it is noted here until done is called.
func (e *Env) sayInstead(q db.Q, rid int64) (done func()) {
	check(db.MarkFounderSaid(q, rid))
	e.init()
	e.mu.Lock()
	e.saying[rid] = true
	e.mu.Unlock()
	return func() {
		e.mu.Lock()
		delete(e.saying, rid)
		e.mu.Unlock()
	}
}

func (e *Env) sayingNow(rid int64) bool {
	e.init()
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.saying[rid]
}

// founderText is what is typed for a founder's message: the message it replies to, when it is a reply, then the words as written, then
// the paths of its pictures. A message that starts with `!` is a command typed as written: no reference line, so the `!` stays first.
func founderText(q db.Q, row *db.Request, images string) string {
	reply := inReplyTo(q, row)
	if strings.HasPrefix(row.Body, "!") {
		reply = ""
	}
	if reply != "" {
		reply = "(" + reply + ")\n"
	}
	return strings.TrimSpace(reply + strings.TrimSpace(textsafe.Lines(row.Body)) + "\n" + images)
}
