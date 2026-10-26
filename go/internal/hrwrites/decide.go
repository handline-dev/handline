package hrwrites

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/textsafe"
)

// Env is what the decision runs on.
type Env struct {
	DB   *sql.DB
	Home string
	// Say tells the proposer how it went (the telling layer); nil tells nobody. The decision is made either way.
	Say *supervisor.Env
	// Live is the agents with a running session; ok is false when the process table could not be read. A rename needs it, and without it is refused.
	Live func() (sessions map[string]bool, ok bool)
	Log  func(format string, args ...any)
}

func (e *Env) logf(format string, args ...any) {
	if e.Log != nil {
		e.Log(format, args...)
	}
}

// Register adds hr.decide, agent.set, hr.withdraw and agent.retire to the server's table.
func Register(s *rpc.Server, e *Env) {
	s.Register("hr.decide", rpc.Method{Role: "member", Write: true, Handler: e.decideMethod})
	s.Register("agent.set", rpc.Method{Role: "agent", Write: true, Handler: e.agentSetMethod})
	s.Register("hr.propose", rpc.Method{Role: "agent", Write: true, Handler: e.proposeMethod})
	s.Register("hr.apply_now", rpc.Method{Role: "member", Write: true, Handler: e.applyNowMethod})
	s.Register("hr.withdraw", rpc.Method{Role: "agent", Write: true, Handler: e.withdrawMethod})
	s.Register("team.seal", rpc.Method{Role: "founder", Write: true, Unkept: true, OwnEvent: true, Handler: e.teamSealMethod})
	s.Register("agent.retire", rpc.Method{Role: "founder", Write: true, Handler: e.retireMethod})
}

const decisionsNS = "hr-decisions"

// recordDecision keeps the founder's answer where no agent can write it; `call` is "approved" or "rejected" and subject what it was decided on.
func (e *Env) recordDecision(pid, call, subject string) error {
	text := fmt.Sprintf(`{"task": 0, "call": %s, "subject": %s, "at": %d}`, rpc.PyJSON(call, false, true), rpc.PyJSON(subject, false, true), time.Now().Unix())
	return protected.New(e.Home).Put(decisionsNS, pid, text)
}

func (e *Env) decisionOf(pid string) map[string]any {
	raw, ok := protected.New(e.Home).Get(decisionsNS, pid)
	if !ok {
		return nil
	}
	var m map[string]any
	if json.Unmarshal([]byte(raw), &m) != nil {
		return nil
	}
	return m
}

// open is (proposal, refusal) for a proposal that can be read at all.
func (e *Env) open(pid string) (*Prop, string) {
	p, err := e.load(pid)
	if err != nil {
		return nil, fmt.Sprintf("proposal %s cannot be read: %v", pid, err)
	}
	if p == nil {
		return nil, "no proposal " + pid
	}
	if !isProposal(p) {
		return nil, pid + " is not a proposal this process knows"
	}
	return p, ""
}

// tell sends the outcome as a board notice, not a founder message, so it asks nothing and a batch arrives as one digest. A failed notice never undoes the decision.
func (e *Env) tell(p *Prop, text string) {
	if e.Say == nil {
		return
	}
	for _, who := range e.concerned(p) {
		e.notify(p, who, text)
	}
}

// concerned is the proposer and the registered agents the proposal or its items name, once each.
func (e *Env) concerned(p *Prop) []string {
	var out []string
	add := func(name string) {
		if name == "" || name == db.Human || slices.Contains(out, name) {
			return
		}
		if _, err := os.Stat(e.roster().AgentFile(name)); err == nil {
			out = append(out, name)
		}
	}
	add(p.str("requested_by"))
	for _, it := range append([]*Prop{p}, p.items()...) {
		add(it.str("agent"))
		add(it.str("to"))
	}
	return out
}

func (e *Env) notify(p *Prop, who, text string) {
	err := func() error {
		tx, err := e.DB.Begin()
		if err != nil {
			return err
		}
		defer tx.Rollback()
		rid, err := e.Say.Notice(tx, who, text, "hr outcome")
		if err != nil {
			return err
		}
		// The notice names its proposal (chat's `proposal`), so an app that draws the decided proposal can leave this line out.
		if pid, ok := hexNum(p.id()); ok && isAlnum(p.id()) {
			if _, err := db.AddEventDeduped(tx, db.Human, "hr_outcome", fmt.Sprintf("hr_outcome:%d", rid), db.KV{K: "proposal", V: pid}, db.KV{K: "request", V: rid}); err != nil {
				return err
			}
		}
		return tx.Commit()
	}()
	if err != nil {
		e.logf("  telling %s about proposal %s failed: %v", who, p.id(), err)
	}
}

// proposalNamed is the proposal a call's `proposal` number names: bad_request for a number that is none, not_found for one no proposal has.
func (e *Env) proposalNamed(params map[string]any) (*Prop, error) {
	pid, ok := rpc.AsInt(params["proposal"])
	if !ok || pid < 0 {
		return nil, rpc.BadRequestError("proposal is a proposal id", "proposal", rpc.InvalidParams)
	}
	for _, p := range e.listing() {
		if n, ok := hexNum(p.id()); ok && isAlnum(p.id()) && n == pid {
			return p, nil
		}
	}
	err := rpc.NotFoundError(fmt.Sprintf("no proposal %d", pid))
	err.Data = map[string]any{"what": "proposal", "name": strconv.FormatInt(pid, 10)}
	return nil, err
}

// applyNowMethod is hr.apply_now: it marks an applying proposal so the next sweeps interrupt the turns it waits on, close those
// sessions and apply it, instead of waiting for the turns to end.
func (e *Env) applyNowMethod(c *rpc.Conn, params map[string]any) (any, error) {
	prop, err := e.proposalNamed(params)
	if err != nil {
		return nil, err
	}
	if st := prop.str("state"); st != applying {
		return nil, e.refusal("proposal_settled", fmt.Sprintf("proposal %s is %s, not waiting to be applied", prop.id(), st), prop)
	}
	if prop.str("apply_now") == "" {
		prop.set("apply_now", time.Now().Unix())
		if err := e.save(prop); err != nil {
			return nil, err
		}
	}
	said := fmt.Sprintf("proposal %s is applied as soon as %s stops", prop.id(), strings.ReplaceAll(prop.str("waiting_on"), ",", ", "))
	return c.Effect(func(*sql.Tx) (map[string]any, error) {
		return map[string]any{"accepted": true, "said": said}, nil
	})
}

// withdrawMethod is hr.withdraw (hr.withdraw): the proposal's own author, or the founder, takes it back before it is applied. The state is read
// and set under one write lock, so a decision landing at the same moment wins or loses cleanly.
func (e *Env) withdrawMethod(c *rpc.Conn, params map[string]any) (any, error) {
	prop, err := e.proposalNamed(params)
	if err != nil {
		return nil, err
	}
	if me := c.Caller.Actor(); me != db.Human && me != prop.str("requested_by") {
		return nil, rpc.Refused(fmt.Sprintf("proposal %s is %s's to withdraw, not yours", prop.id(), prop.str("requested_by")), "role", "")
	}
	ctx := context.Background()
	conn, err := e.DB.Conn(ctx)
	if err != nil {
		return nil, err
	}
	if _, err := conn.ExecContext(ctx, "BEGIN IMMEDIATE"); err != nil {
		conn.Close()
		return nil, err
	}
	finish := func(stmt string) { conn.ExecContext(ctx, stmt); conn.Close() }
	if again, err := e.load(prop.id()); err != nil {
		finish("ROLLBACK")
		return nil, err
	} else if again != nil {
		prop = again
	}
	if st := prop.str("state"); st != "proposed" && st != "approved" {
		finish("ROLLBACK")
		return nil, e.refusal("proposal_settled", fmt.Sprintf("proposal %s is already %s - nothing to withdraw", prop.id(), st), prop)
	}
	prop.set("state", "withdrawn")
	prop.set("decided", time.Now().Unix())
	if err := e.save(prop); err != nil {
		finish("ROLLBACK")
		return nil, err
	}
	finish("COMMIT")
	return c.Effect(func(*sql.Tx) (map[string]any, error) {
		return map[string]any{"accepted": true, "said": fmt.Sprintf("proposal %s withdrawn", prop.id())}, nil
	})
}

// decide is the founder's decision on a proposal: call is "approved" or "rejected". An approve is applied now and the proposer told the outcome; a
// reject is recorded and told. The decision is recorded in the protected store, where no agent can write it, and apply acts on that record alone.
// An approve needs the card's digest the founder read, so an edit made after it was shown is never approved unseen. Approving a proposal that was
// approved but did not apply retries it.
func (e *Env) decide(pid, call, by, note, subject string, tell bool) (bool, string, error) {
	if call != "approved" && call != "rejected" {
		return false, "decide `approved` or `rejected`", nil
	}
	if by != db.Human {
		return false, fmt.Sprintf("proposal %s is the founder's to decide, not %s's", pid, by), nil
	}
	if call == "approved" && subject == "" {
		return false, "approve what was read: pass the card's `subject`", nil
	}
	note = textsafe.Oneline(strings.TrimSpace(note))
	prop, why := e.open(pid)
	if why != "" {
		return false, why, nil
	}
	// One decision wins: the state is read, the answer recorded and the state set under one write lock, so a withdraw or a second answer finds this one already there.
	ctx := context.Background()
	conn, err := e.DB.Conn(ctx)
	if err != nil {
		return false, "", err
	}
	if _, err := conn.ExecContext(ctx, "BEGIN IMMEDIATE"); err != nil {
		conn.Close()
		return false, "", err
	}
	finish := func(stmt string) { conn.ExecContext(ctx, stmt); conn.Close() }
	if again, err := e.load(pid); err != nil {
		finish("ROLLBACK")
		return false, "", err
	} else if again != nil {
		prop = again
	}
	if st := prop.str("state"); st != "proposed" && st != "approved" {
		finish("ROLLBACK")
		return false, fmt.Sprintf("proposal %s is already %s", pid, st), nil
	}
	if call == "approved" && subject != prop.subject() {
		finish("ROLLBACK")
		return false, fmt.Sprintf("proposal %s changed after you read it - read it again before approving", pid), nil
	}
	if got := e.decisionOf(pid); prop.str("state") == "approved" && call == "approved" && got != nil && got["call"] == "approved" && got["subject"] == prop.subject() {
		finish("ROLLBACK")
		return e.apply(pid, tell)
	}
	if err := e.recordDecision(pid, call, prop.subject()); err != nil {
		finish("ROLLBACK")
		return false, err.Error(), nil
	}
	prop.set("state", call)
	verdict := note
	if verdict == "" {
		verdict = call
	}
	prop.set("verdict", verdict)
	if note != "" {
		prop.set("note", note) // the founder's own words, apart from the verdict's call word or system text
	}
	prop.set("decided", time.Now().Unix())
	if err := e.save(prop); err != nil {
		finish("ROLLBACK")
		return false, "", err
	}
	finish("COMMIT")
	if call == "rejected" {
		if tell {
			text := fmt.Sprintf("proposal %s was rejected", pid)
			if note != "" {
				text += ": " + note
			} else {
				text += "."
			}
			e.tell(prop, text)
		}
		return true, fmt.Sprintf("proposal %s rejected", pid), nil
	}
	return e.apply(pid, tell)
}

// apply carries out a proposal the founder approved. It refuses anything else, and anything whose recorded approval is not for the draft as it is now.
// A change to an agent with a session running is held until its turn ends (SweepHeld applies it then).
func (e *Env) apply(pid string, tell bool) (bool, string, error) {
	prop, why := e.open(pid)
	if why != "" {
		return false, why, nil
	}
	switch state := prop.str("state"); {
	case state == "applied":
		return false, fmt.Sprintf("proposal %s was already applied on %s", pid, stamp(prop.get("applied"))), nil
	case state != "approved":
		return false, fmt.Sprintf("proposal %s is %s, not approved - the founder decides it in the app's HR tab", pid, state), nil
	}
	if why := e.unapproved(prop); why != "" {
		return false, why, nil
	}
	if busy := e.heldBy(prop); len(busy) > 0 {
		if err := e.hold(prop, busy); err != nil {
			return false, "", err
		}
		said := fmt.Sprintf("proposal %s approved: it is applied when %s ends the turn running now", pid, strings.Join(busy, ", "))
		if tell {
			e.tell(prop, said+".")
		}
		return true, said, nil
	}
	ok, said, err := e.run(prop)
	if err != nil {
		return false, "", err
	}
	return e.record(prop, ok, said, tell)
}

// unapproved is why prop may not be applied: no approval from the founder on record, or one for another draft. "" when it may.
func (e *Env) unapproved(prop *Prop) string {
	pid := prop.id()
	got := e.decisionOf(pid)
	if got == nil || got["call"] != "approved" {
		return fmt.Sprintf("no approval from the founder is on record for proposal %s - it was not decided by the founder; raise it again", pid)
	}
	if got["subject"] != prop.subject() {
		return fmt.Sprintf("proposal %s was changed after the founder approved it - what they approved is not what is in the draft now; raise it again", pid)
	}
	return ""
}

// record keeps how an apply went (applied, or approved with the error, which hr.list shows as apply_failed) and tells the proposer.
func (e *Env) record(prop *Prop, ok bool, said string, tell bool) (bool, string, error) {
	pid := prop.id()
	prop.pop("waiting_on")
	prop.pop("waiting_since")
	prop.pop("apply_now")
	if !ok {
		prop.set("state", "approved")
		prop.set("error", said)
		if err := e.save(prop); err != nil {
			return false, "", err
		}
		if tell {
			e.tell(prop, fmt.Sprintf("proposal %s was approved but could not be applied: %s", pid, said))
		}
		return false, said, nil
	}
	prop.set("state", "applied")
	prop.set("applied", time.Now().Unix())
	prop.pop("error")
	if err := e.save(prop); err != nil {
		return false, "", err
	}
	if tell {
		e.tell(prop, fmt.Sprintf("proposal %s applied: %s", pid, said))
	}
	return true, said, nil
}

// stamp is a timestamp in local time, "an unknown date" for none.
func stamp(v any) string {
	n, _ := v.(int64)
	if n == 0 {
		return "an unknown date"
	}
	return time.Unix(n, 0).Format("2006-01-02 15:04")
}

// ---------------------------------------------------------------- the method

func (e *Env) reader() *reads.Env { return &reads.Env{DB: e.DB, Home: e.Home} }

// hexNum is the wire's id of a store id (hex text).
func hexNum(pid string) (int64, bool) {
	n, err := strconv.ParseInt(pid, 16, 64)
	return n, err == nil
}

func isAlnum(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if !(r >= '0' && r <= '9' || r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z') {
			return false
		}
	}
	return true
}

// refusal is a refusal that carries the proposal as it is, for the card to be read again.
func (e *Env) refusal(gate, why string, p *Prop) *rpc.Error {
	err := rpc.Refused(why, gate, "")
	err.Data["proposal"] = e.reader().HRView(p.id())
	return err
}

func (e *Env) decideMethod(c *rpc.Conn, params map[string]any) (any, error) {
	bad := func(message, field string) error { return rpc.BadRequestError(message, field, rpc.InvalidParams) }
	decision, _ := params["decision"].(string)
	if decision != "approve" && decision != "reject" {
		return nil, bad("decision is approve or reject", "decision")
	}
	note, hasNote := params["note"].(string)
	if params["note"] != nil && !hasNote {
		return nil, bad("note is text", "note")
	}
	subject, hasSubject := params["subject"].(string)
	if params["subject"] != nil && !hasSubject {
		return nil, bad("subject is the digest hr.list showed", "subject")
	}
	prop, err := e.proposalNamed(params)
	if err != nil {
		return nil, err
	}
	if decision == "approve" && subject == "" {
		return nil, bad("an approve names the subject hr.list showed", "subject")
	}
	state := ""
	if view := e.reader().HRView(prop.id()); view != nil {
		state, _ = view["state"].(string)
	}
	if state != "open" && state != "apply_failed" { // decided, applied or taken back already: nothing to decide (checked before the digest)
		shown := state
		if shown == "" {
			shown = prop.str("state")
		}
		return nil, e.refusal("proposal_settled", fmt.Sprintf("proposal %s is already %s", prop.id(), shown), prop)
	}
	if decision == "approve" && subject != prop.subject() {
		return nil, e.refusal("proposal_changed", fmt.Sprintf("proposal %s changed after you read it: read it again before approving", prop.id()), prop)
	}
	call := "rejected"
	if decision == "approve" {
		call = "approved"
	}
	if subject == "" {
		subject = prop.subject()
	}
	done, said, err := e.decide(prop.id(), call, db.Human, note, subject, true)
	if err != nil {
		return nil, err
	}
	if !done {
		return nil, rpc.Refused(strings.TrimSpace(said), "", "")
	}
	return c.Effect(func(*sql.Tx) (map[string]any, error) {
		return map[string]any{"accepted": true, "said": said}, nil
	})
}
