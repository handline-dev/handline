package supervisor

import (
	"fmt"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/attachments"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

// Results told to whoever opened the work reach them in one digest, not one wake each (daemon/noticedigest.py).
//
// A notice (delivery.ClassBatch: a task done or discarded, a child's result, a task change, a memo) is information: it never wakes anyone and
// is never typed mid-turn. It rides on the target's next real delivery, and the first notice starts a clock: once it has run the notice digest
// window, the notices still waiting are typed as one message of one line each, the next time the tick finds the target at its prompt with a
// live session. A closed agent keeps the clock: a notice is not worth waking it for.
//
// What goes wrong is not information. A task that ends FAIL, blocked or FATAL, and an agent the daemon finds FATAL or blocked, are told to the
// agents waiting on them at once, as their own message, not held for a digest. Both are read as levels from the database each tick.

const (
	DigestMaxLines = 20
	ResultShown    = 80
)

var resultNotice = regexp.MustCompile(`(?s)\AT(\d+) (done|discarded) by (.+?):\s*(.*)\z`)

// say writes one message to `to` from the task board, written and queued: not a notice, so it wakes a closed agent once told.
func (e *Env) say(q db.Q, to, text, reason string) int64 {
	rid := must(db.AddRequest(q, db.NewRequest{Requester: db.NoticeRequester, Target: to, Body: text, Response: "fire_and_forget", Kind: "say"}))
	if err := e.deliver(rid, db.NoticeRequester, to, text, "", team.Decision{Allowed: true, Reason: reason, Kind: ptr("say"), Response: "fire_and_forget"}); err != nil {
		e.log("  request %d: the inbox file for %s was not written: %v", rid, to, err)
	}
	must(db.AddEvent(q, db.NoticeRequester, "notice_told", db.KV{K: "request_id", V: rid}, db.KV{K: "to", V: to}, db.KV{K: "reason", V: reason}))
	return rid
}

// Notice writes one notice from the task board to `to` (delivery.ClassBatch): it waits for the next digest or rides on the next delivery.
func (e *Env) Notice(q db.Q, to, text, reason string) (rid int64, err error) {
	defer Catch(&err)
	rid = must(db.AddRequest(q, db.NewRequest{Requester: db.NoticeRequester, Target: to, Body: text, Response: "fire_and_forget", Kind: "say", Notice: true}))
	if err := e.deliver(rid, db.NoticeRequester, to, text, "", team.Decision{Allowed: true, Reason: reason, Kind: ptr("say"), Response: "fire_and_forget"}); err != nil {
		e.log("  request %d: the inbox file for %s was not written: %v", rid, to, err)
	}
	return rid, nil
}

func (e *Env) tellNowSay(q db.Q, to, text, reason string) {
	rid := e.say(q, to, text, reason)
	must(e.TellNow(q, rid))
}

// DigestLine is `T<n> · who · result (msg_<hash>)` for a result notice; any other notice is its own text. A memo's file lines follow, one each, so
// every path can be read whole.
func DigestLine(q db.Q, r *db.Request) string {
	text, files := attachments.SplitFileLines(r.Body)
	line := digestSummary(text, r.Name(q))
	for _, f := range files {
		line += "\n  " + textsafe.Oneline(f)
	}
	return line
}

func digestSummary(text, name string) string {
	body := oneSpace(textsafe.Oneline(text))
	m := resultNotice.FindStringSubmatch(body)
	if m == nil {
		return fmt.Sprintf("%s (%s)", firstRunes(body, ResultShown+30), name)
	}
	tid, how, who, result := m[1], m[2], m[3], m[4]
	if how != "done" {
		result = "discarded: " + result
	}
	if utf8.RuneCountInString(result) > ResultShown {
		result = strings.TrimRight(string([]rune(result)[:ResultShown]), " \t\n\r\v\f") + "…"
	}
	return fmt.Sprintf("T%s · %s · %s (%s)", tid, who, result, name)
}

func firstRunes(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

// DigestText is the one message of a waiting agent's results.
func DigestText(q db.Q, rows []*db.Request) string {
	shown := rows
	if len(shown) > DigestMaxLines {
		shown = rows[:DigestMaxLines]
	}
	left := len(rows) - len(shown)
	plural := "s"
	if len(rows) == 1 {
		plural = ""
	}
	head := fmt.Sprintf("%s%d result%s since %s - `hl read <id>` shows one whole:", db.DigestHead, len(rows), plural,
		time.Unix(rows[0].LandedAt.Int64, 0).Format("15:04"))
	lines := []string{head}
	for _, r := range shown {
		lines = append(lines, DigestLine(q, r))
	}
	if left > 0 {
		lines = append(lines, fmt.Sprintf("(+%d more: `hl inbox --all`)", left))
	}
	return strings.Join(lines, "\n")
}

// atPrompt: a live session at its prompt, not mid-turn and not closed. Background work it left running does not hold a digest back.
func (e *Env) atPrompt(q db.Q, agent string) bool {
	row := stateOf(q, agent)
	return row != nil && row.State == states.Idle && !(row.ClosedAt.Valid && row.ClosedAt.Int64 != 0) && e.sessionPresent(agent)
}

// SweepDigests types one digest to each agent whose oldest waiting notice has run its window out, once it is at its prompt: an agent mid-turn
// waits for the turn's end.
func (e *Env) SweepDigests(now int64) (err error) {
	defer Catch(&err)
	q := db.Q(e.DB)
	for _, w := range must(db.AgentsWithNotices(q)) {
		if !e.registered(w.Agent) || now-w.Since < e.Idle.NoticeDigestS(w.Agent) || !e.atPrompt(q, w.Agent) {
			continue
		}
		rows := must(db.PendingNotices(q, w.Agent))
		if len(rows) == 0 {
			continue
		}
		// Written and marked before it is told: a tell that raises leaves the digest queued for TellUndelivered, and no second digest is built.
		rid := e.say(q, w.Agent, DigestText(q, rows), "notice digest")
		ids := requestIDs(rows)
		check(db.MarkNoticesDigested(q, ids))
		must(db.AddEvent(q, db.NoticeRequester, "notices_digested", db.KV{K: "request_id", V: rid}, db.KV{K: "to", V: w.Agent}, db.KV{K: "notices", V: ids}))
		must(e.TellNow(q, rid))
		e.log("  %s: %d waiting result(s) told in one digest (request %d)", w.Agent, len(rows), rid)
	}
	return nil
}

// waitingOn is who is waiting on the agent: whoever opened a task it owns, or asked it something still queued.
func (e *Env) waitingOn(q db.Q, agent string) []string {
	known := map[string]bool{}
	for _, a := range e.agents() {
		known[a] = true
	}
	var who []string
	for _, t := range must(db.OpenTasks(q)) {
		if t.Owner == agent {
			who = append(who, t.OpenedBy)
		}
	}
	for _, r := range must(db.QueuedFor(q, agent)) {
		who = append(who, r.Requester)
	}
	var out []string
	seen := map[string]bool{}
	for _, w := range who {
		if known[w] && w != agent && !seen[w] {
			seen[w] = true
			out = append(out, w)
		}
	}
	return out
}

func (e *Env) tellWaiting(q db.Q, agent, text, reason string) {
	for _, who := range e.waitingOn(q, agent) {
		e.tellNowSay(q, who, text, reason)
		e.log("  %s: told at once that %s %s", who, agent, reason)
	}
}

// SweepStopped: an agent that is FATAL, or whose work cannot be given (it is on the founder's list), is told once per spell to whoever waits on
// it; the mark ends with the spell.
func (e *Env) SweepStopped(now int64) (err error) {
	defer Catch(&err)
	q := db.Q(e.DB)
	stalled := map[string]*db.AgentState{}
	for _, r := range must(db.AllAgentStates(q)) {
		if r.State == states.Stalled {
			stalled[r.Agent] = r
		}
	}
	blocks := map[string]*db.Block{}
	for _, b := range must(db.Blocks(q)) {
		if now >= b.ShowAt {
			blocks[b.Agent] = b
		}
	}
	agents := append([]string(nil), e.agents()...)
	sortStrings(agents)
	for _, kind := range []string{"fatal", "blocked"} {
		for _, agent := range agents {
			key := "stopped_told:" + kind + ":" + agent
			_, in := stalled[agent]
			if kind == "blocked" {
				_, in = blocks[agent]
			}
			if !in {
				if must(db.Cursor(q, key)) != 0 {
					check(db.SetCursor(q, key, 0))
				}
				continue
			}
			if must(db.Cursor(q, key)) != 0 {
				continue
			}
			check(db.SetCursor(q, key, 1))
			// A block's detail can be empty (a screen that could not be read): its reason says what is in the way.
			var detail string
			if kind == "fatal" {
				detail = stalled[agent].Detail.String
			} else {
				detail = e.Dialogs.Reason(blocks[agent].Kind, blocks[agent].Detail)
			}
			detail = firstRunes(oneSpace(textsafe.Oneline(detail)), 300)
			what := "FATAL"
			if kind == "blocked" {
				what = "blocked"
			} else if strings.HasPrefix(detail, DialogStall) {
				what = "stalled"
			}
			e.tellWaiting(q, agent, fmt.Sprintf("%s is %s: %s", agent, what, detail), "is "+what)
		}
	}
	return nil
}

// SweepNotices is noticedigest.sweep.
func (e *Env) SweepNotices(now int64) error {
	if err := e.SweepStopped(now); err != nil {
		return err
	}
	if err := e.SweepDigests(now); err != nil {
		return err
	}
	return e.SweepHeldMemos(now)
}
