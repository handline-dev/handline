package team

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// The founder answering a trust proposal (team/trust.py apply and settle_moot, decisions.py, ask.py _apply_answered). The answer is
// recorded in the protected store as it is given, and apply acts on that record and the decision task, never on the request row alone.

const trustDecisionsNS = "trust-decisions"

var (
	approveWords = []string{"approve", "approved", "yes", "ok", "go", "승인"}
	rejectWords  = []string{"reject", "rejected", "no", "deny", "denied", "반려"}
)

// CallOf is decisions.call_of: approved, rejected or unclear, from the first word of the verdict.
func CallOf(verdict string) string {
	fields := strings.Fields(verdict)
	if len(fields) == 0 {
		return "unclear"
	}
	word := strings.ToLower(strings.Trim(fields[0], ".,!:;\"'`"))
	switch {
	case contains(approveWords, word):
		return "approved"
	case contains(rejectWords, word):
		return "rejected"
	}
	return "unclear"
}

// TrustProposals is the supervisor's Proposals port over one home's trust store and database.
type TrustProposals struct {
	Trust *Trust
	Q     db.Q
}

// OpenProposalOf is trust.open_proposal_of: the open proposal whose decision is request rid (its decision task's card), "" for none.
func (p TrustProposals) OpenProposalOf(rid int64) string {
	list, err := p.Trust.Listing()
	if err != nil {
		return ""
	}
	tid, err := db.TaskOfCard(p.Q, rid)
	if err != nil {
		return ""
	}
	for _, prop := range list {
		if prop.Get("state") != "proposed" {
			continue
		}
		if task, ok := prop.Get("task").(int64); ok && task != 0 {
			if task == tid {
				return pyStr(prop.Get("id"))
			}
			continue
		}
		if n, ok := prop.Get("request").(int64); ok && n == rid {
			return pyStr(prop.Get("id"))
		}
	}
	return ""
}

// Answered is ask._apply_answered: the founder answered proposal pid on card. An approve is applied now and its outcome appended to the
// answer the proposer is told (line); one that fails is also reported to the founder (report) and the proposal stays proposed, so
// `hl trust apply` can still settle it. A reject is only recorded on the proposal's file.
func (p TrustProposals) Answered(q db.Q, pid string, card *db.Request, verdict string) (line, report string, err error) {
	call := CallOf(verdict)
	if call != "approved" && call != "rejected" {
		return "", "", nil
	}
	prop, ok, err := p.Trust.LoadProposal(pid)
	if err != nil {
		return fmt.Sprintf("proposal %s cannot be read: %v", pid, err), "", nil
	}
	if !ok {
		return "no proposal " + pid, "", nil
	}
	subject := SubjectOf(prop)
	if call == "approved" {
		read, ok := RootRead(card.Body, card.Requester)
		if !ok {
			return fmt.Sprintf("proposal %s was not applied: #%d does not name the root it asked the founder to approve", pid, card.ID), "", nil
		}
		subject = read
		if r, ok := resolved(read); ok {
			subject = r
		}
	}
	tid, err := db.TaskOfCard(q, card.ID)
	if err != nil {
		return "", "", err
	}
	if err := p.Trust.recordDecision(pid, tid, call, subject); err != nil {
		return fmt.Sprintf("proposal %s was not applied: %v", pid, err), "", nil
	}
	applied, said, err := p.Trust.Apply(q, pid)
	if err != nil || call == "rejected" {
		return "", "", err
	}
	if applied {
		line = fmt.Sprintf("proposal %s applied: %s", pid, said)
	} else {
		line = fmt.Sprintf("proposal %s could not be applied: %s", pid, said)
		report = fmt.Sprintf("%s\nThe proposal is still open; `hl trust apply %s` retries it.", line, pid)
	}
	return line, report, db.AppendVerdict(q, card.ID, line)
}

// recordDecision is decisions.record: the founder's call on proposal pid, answered on decision task tid, and what it was decided on.
func (t *Trust) recordDecision(pid string, tid int64, call, subject string) error {
	return t.Store.Put(trustDecisionsNS, pid, fmt.Sprintf(`{"task": %d, "call": %s, "subject": %s, "at": %d}`,
		tid, rpc.PyJSON(call, false, true), rpc.PyJSON(subject, false, true), time.Now().Unix()))
}

// decisionRefusal is decisions.refusal: why the recorded answer to decision task tid cannot be relied on, "" when it names this task, a
// decision, and the draft as it is now. A record from before proposals were keyed by task names the card (cardID) instead.
func (t *Trust) decisionRefusal(pid string, tid int64, subject string, cardID int64) string {
	got, ok := decodeObject(t.Store.Get(trustDecisionsNS, pid))
	if !ok {
		return fmt.Sprintf("no answer from the founder is on record for proposal %s - it was answered before answers were recorded, or not by the founder; raise it again", pid)
	}
	var namesIt bool
	if task, has := got["task"]; has {
		namesIt = task == float64(tid)
	} else {
		namesIt = cardID != 0 && got["request"] == float64(cardID)
	}
	if c := got["call"]; !namesIt || c != "approved" && c != "rejected" {
		return fmt.Sprintf("the founder's recorded answer for proposal %s is not for T%d", pid, tid)
	}
	if got["subject"] != subject {
		return fmt.Sprintf("proposal %s was changed after the founder answered it - what they approved is not what is in the draft now; raise it again", pid)
	}
	return ""
}

// answerOf is decisions.answer_of: none (no task to read), open (not answered), discarded (withdrawn, handed back, thrown away or
// closed by someone but the founder: nothing was decided) or answered, with the task and the founder's verdict.
func answerOf(q db.Q, prop Proposal) (state string, task *db.Task, verdict string, err error) {
	tid, _ := prop.Get("task").(int64)
	if tid == 0 {
		rid, _ := prop.Get("request").(int64)
		if tid, err = db.TaskOfCard(q, rid); err != nil {
			return "", nil, "", err
		}
	}
	if tid != 0 {
		if task, err = db.GetTask(q, tid); err != nil {
			return "", nil, "", err
		}
	}
	switch {
	case task == nil:
		return "none", nil, "", nil
	case task.DiscardedBy.String != "":
		return "discarded", task, "", nil
	case task.DoneEvidence.String != "":
		mine, err := db.FounderClosed(q, task)
		if err != nil || !mine {
			return "discarded", task, "", err
		}
		return "answered", task, task.DoneEvidence.String, nil
	}
	return "open", task, "", nil
}

// Apply is trust.apply: carry out a proposal the founder answered. ok is true only when the root was declared.
func (t *Trust) Apply(q db.Q, pid string) (ok bool, said string, err error) {
	prop, found, err := t.LoadProposal(pid)
	if err != nil {
		return false, "", err
	}
	if !found {
		return false, "no proposal " + pid, nil
	}
	if prop.Get("state") == "applied" {
		return false, fmt.Sprintf("proposal %s was already applied", pid), nil
	}
	state, task, verdict, err := answerOf(q, prop)
	if err != nil {
		return false, "", err
	}
	switch state {
	case "none":
		return false, fmt.Sprintf("proposal %s has no decision to check - raise it again", pid), nil
	case "open":
		return false, fmt.Sprintf("the founder has not answered T%d yet. It is on `hl`, top panel.", task.ID), nil
	case "discarded":
		why := ""
		if r := strings.TrimSpace(task.DiscardReason.String); r != "" {
			why = " (" + r + ")"
		}
		return false, fmt.Sprintf("T%d, the decision on proposal %s, was discarded%s or handed back or closed by someone but the founder - nothing was decided. Raise it again.",
			task.ID, pid, why), nil
	}
	cards, err := db.TaskRequests(q, task.ID)
	if err != nil {
		return false, "", err
	}
	var card *db.Request
	if len(cards) > 0 {
		card = cards[0]
	}
	var cardID int64
	if card != nil {
		cardID = card.ID
	}
	call := CallOf(verdict)
	if call == "approved" || call == "rejected" {
		if why := t.decisionRefusal(pid, task.ID, SubjectOf(prop), cardID); why != "" {
			return false, why, nil
		}
		if got, _ := decodeObject(t.Store.Get(trustDecisionsNS, pid)); got["call"] != call {
			return false, fmt.Sprintf("the founder's recorded answer for proposal %s does not match T%d's answer", pid, task.ID), nil
		}
	}
	switch call {
	case "rejected":
		prop = pairsSet(prop, "state", "rejected")
		prop = pairsSet(prop, "verdict", textsafe.Oneline(verdict))
		if err := t.SaveProposal(prop); err != nil {
			return false, "", err
		}
		return false, fmt.Sprintf("proposal %s was rejected: %s", pid, firstRunes(strings.TrimSpace(verdict), 120)), nil
	case "unclear":
		return false, fmt.Sprintf("T%d was answered %s, which is not a decision.", task.ID, pyQuote(firstRunes(textsafe.Oneline(strings.TrimSpace(verdict)), 60))), nil
	}
	// What gets added is exactly what the founder read: the path on the card's first line, which the proposal file cannot edit.
	root := pyStr(prop.Get("root"))
	read, readOK := "", false
	if card != nil {
		read, readOK = RootRead(card.Body, card.Requester)
	}
	if want, _ := resolved(root); !readOK || resolvedOr(read) != want {
		return false, fmt.Sprintf("proposal %s's root does not match what #%d asked the founder to approve - raise it again", pid, cardID), nil
	}
	if t.IsDeclared(root) {
		if _, err := t.SettleMoot(); err != nil {
			return false, "", err
		}
		return false, fmt.Sprintf("proposal %s is moot: %s is already a declared root", pid, root), nil
	}
	row := newJobj()
	row.set("root", root)
	row.set("why", prop.Get("why"))
	row.set("added_by", prop.Get("requested_by"))
	created, _ := prop.Get("created").(int64)
	row.set("added_at", jsonInt(created))
	if err := t.save(append(t.roots(), row)); err != nil {
		return false, err.Error() + ". The founder's session applies it when they answer", nil
	}
	prop = pairsSet(prop, "state", "applied")
	prop = pairsSet(prop, "applied", time.Now().Unix())
	prop = pairsSet(prop, "verdict", textsafe.Oneline(verdict))
	if err := t.SaveProposal(prop); err != nil {
		return false, "", err
	}
	return true, root + " is now a declared root", nil
}

// decodeObject is a protected entry read as a JSON object; ok is false when there is none or it is not one.
func decodeObject(raw string, ok bool) (map[string]any, bool) {
	var m map[string]any
	if !ok || json.Unmarshal([]byte(raw), &m) != nil || m == nil {
		return nil, false
	}
	return m, true
}

func resolvedOr(p string) string {
	if r, ok := resolved(p); ok {
		return r
	}
	return ""
}

func firstRunes(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}
