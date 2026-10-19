package supervisor

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/peerinbox"
	"github.com/handline/handline/go/internal/textsafe"
)

// The founder's own words, natively (FounderTyped): a message the founder sends an agent is typed into its terminal as its person's prompt,
// with no envelope and no instructions, and what the agent writes after it in that turn is the reply. Everything else still reaches the
// agent as a peer's message, so a typed prompt is always the founder's.

// typedRow is whether row is a founder's message to an agent that takes them typed: one the founder's own connection sent
// (db.FounderAuthored), never one that only names the founder as its requester.
func (e *Env) typedRow(q db.Q, row *db.Request) bool {
	return e.FounderTyped != nil && row.Requester == db.Human && row.Kind.String == sayKind && row.Target != db.Human && e.FounderTyped(row.Target) &&
		must(db.FounderAuthored(q, row.ID))
}

// TypedOp is whether the message op to agent is typed, not sent to its inbox: an op that tells it one typed row.
func (e *Env) TypedOp(q db.Q, agent, op string) bool {
	rids := ridsOfOp(op)
	if len(rids) != 1 {
		return false
	}
	row, err := db.GetRequest(q, rids[0])
	return err == nil && row != nil && row.Target == agent && e.typedRow(q, row)
}

// founderText is what is typed for a founder's message: the words as written, then the paths of its pictures.
func founderText(row *db.Request, images string) string {
	return strings.TrimSpace(strings.TrimSpace(textsafe.Lines(row.Body)) + "\n" + images)
}

// ReplyWindowS is how long after it was typed a founder's message is still looked for in the transcript.
const ReplyWindowS = 24 * 3600

// FounderReplies is what follows a Stop of agent's turn: each typed founder's message that the transcript shows taken in, and that has no
// reply yet, gets the agent's text after it as its reply. One whose turn the transcript does not show ended is pending and left for a
// later read, unless last. What is returned runs once the transaction has committed.
func (e *Env) FounderReplies(q db.Q, agent, transcript string, last bool) (finish []func(), pending bool, err error) {
	defer Catch(&err)
	if e.FounderTyped == nil || !e.FounderTyped(agent) || transcript == "" {
		return nil, false, nil
	}
	rows := must(q.Query("SELECT id FROM requests WHERE requester = ? AND target = ? AND kind = ? AND landed_at IS NOT NULL AND typed_at >= ? "+
		"AND NOT EXISTS (SELECT 1 FROM requests r WHERE r.parent_id = requests.id AND r.requester = ? AND r.target = ?) ORDER BY id",
		db.Human, agent, sayKind, e.now()-ReplyWindowS, agent, db.Human))
	var rids []int64
	for rows.Next() {
		var rid int64
		check(rows.Scan(&rid))
		rids = append(rids, rid)
	}
	check(rows.Err())
	rows.Close()
	for _, rid := range rids {
		key := fmt.Sprintf("founder_reply:%s:%d", agent, rid)
		if must(db.Cursor(q, key)) != 0 {
			continue // read already, and it had no text to reply with
		}
		row := must(db.GetRequest(q, rid))
		reply, found, ended := ReplyIn(transcript, founderText(row, e.imageLines(q, row)), row.TypedAt.Int64)
		if !found {
			continue // typed, and not taken in yet: a later turn reads it
		}
		if !ended && !last {
			pending = true
			continue
		}
		if reply == "" {
			check(db.SetCursor(q, key, 1))
			e.log("  %s: request %d was read and the turn wrote no text to reply with", agent, rid)
			continue
		}
		out := must(e.Say(q, SayIn{Target: db.Human, Body: reply, Requester: agent, ParentID: rid}))
		if !out.OK {
			e.log("  %s: the reply to request %d was not written: %s", agent, rid, out.Message)
			continue
		}
		finish = append(finish, func() { out.Finish() })
		e.log("  %s: replied to request %d from its transcript", agent, rid)
	}
	return finish, pending, nil
}

// ReplyIn is the agent's reply to text typed at it, read from its transcript: the text it wrote after the record that took text in (the
// first one stamped no earlier than typedAt), up to the end of that turn or the next prompt typed at it, as its first and last text
// block. The first is there because an answer written early in a long turn would otherwise be folded away behind a later status line.
// found is false while the transcript shows no such record; ended, whether it shows where the reply ends.
func ReplyIn(transcript, text string, typedAt int64) (reply string, found, ended bool) {
	f, err := os.Open(transcript)
	if err != nil {
		return "", false, false
	}
	defer f.Close()
	var blocks []string
	r := bufio.NewReaderSize(f, 1<<20)
read:
	for {
		line, err := r.ReadString('\n')
		if strings.HasSuffix(line, "\n") {
			switch {
			case !found:
				found = peerinbox.ShowsTyped(line, text) && stampedFrom(line, typedAt)
			case typedPrompt(line) || turnEnd(line):
				ended = true
				break read
			default:
				blocks = append(blocks, textBlocks(line)...)
			}
		}
		if err != nil {
			break
		}
	}
	switch len(blocks) {
	case 0:
		return "", found, ended
	case 1:
		return blocks[0], found, ended
	}
	return blocks[0] + "\n\n" + blocks[len(blocks)-1], found, ended
}

// turnEnd is whether a transcript record is the one the CLI writes once a turn has ended (a Stop hook that sends the turn on writes none).
func turnEnd(line string) bool {
	var rec struct{ Type, Subtype string }
	return json.Unmarshal([]byte(line), &rec) == nil && rec.Type == "system" && rec.Subtype == "turn_duration"
}

// stampedFrom is whether a transcript record's own timestamp is no earlier than at (a second's slack for the clocks); a record with none passes.
func stampedFrom(line string, at int64) bool {
	var rec struct{ Timestamp string }
	if json.Unmarshal([]byte(line), &rec) != nil || rec.Timestamp == "" {
		return true
	}
	ts, err := time.Parse(time.RFC3339Nano, rec.Timestamp)
	return err != nil || ts.Unix() >= at-1
}

// typedPrompt is whether a transcript record is a prompt typed at the session: its person's user record, or one taken into a running turn.
func typedPrompt(line string) bool {
	var rec struct {
		Type       string
		Origin     struct{ Kind string }
		Message    struct{ Content any }
		Attachment struct {
			Type   string
			Origin struct{ Kind string }
		}
	}
	if json.Unmarshal([]byte(line), &rec) != nil {
		return false
	}
	_, typed := rec.Message.Content.(string)
	return rec.Type == "user" && rec.Origin.Kind == "human" && typed ||
		rec.Type == "attachment" && rec.Attachment.Type == "queued_command" && rec.Attachment.Origin.Kind != "peer"
}

// textBlocks are an assistant record's text blocks, trimmed, the empty ones left out.
func textBlocks(line string) []string {
	var rec struct {
		Type    string
		Message struct {
			Content []struct{ Type, Text string }
		}
	}
	if json.Unmarshal([]byte(line), &rec) != nil || rec.Type != "assistant" {
		return nil
	}
	var out []string
	for _, b := range rec.Message.Content {
		if t := strings.TrimSpace(b.Text); b.Type == "text" && t != "" {
			out = append(out, t)
		}
	}
	return out
}

// The CLI writes a turn's last text, and the record that ends the turn, after its Stop hook ran: a reply whose turn has not shown ended is
// read again ReplyWait later, at most ReplyReads times in all, and the last read takes what the transcript shows.
var (
	ReplyWait  = time.Second
	ReplyReads = 10
)

// FounderStopped is the Stop hook of agent's turn, with the transcript it names: FounderReplies, off the hook, each read in a transaction of
// its own and one agent's reads one at a time.
func (e *Env) FounderStopped(agent, transcript string) {
	if e.FounderTyped == nil || !e.FounderTyped(agent) {
		return
	}
	e.background("founder replies of "+agent, func() {
		for i := 1; e.founderRead(agent, transcript, i == ReplyReads); i++ {
			e.sleep(ReplyWait)
		}
	})
}

// founderRead is one read of FounderReplies, and whether a reply is still pending.
func (e *Env) founderRead(agent, transcript string, last bool) bool {
	e.replyMu.Lock()
	defer e.replyMu.Unlock()
	tx, err := e.DB.Begin()
	if err != nil {
		e.log("  %s: reading the founder's replies: %v", agent, err)
		return false
	}
	defer tx.Rollback()
	finish, pending, err := e.FounderReplies(tx, agent, transcript, last)
	if err == nil {
		err = tx.Commit()
	}
	if err != nil {
		e.log("  %s: reading the founder's replies: %v", agent, err)
		return false
	}
	for _, f := range finish {
		f()
	}
	return pending
}
