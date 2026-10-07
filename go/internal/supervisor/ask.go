package supervisor

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

// Requests between agents (team/ask.py): what `hl task new` and `hl say` write. The agent supplies a target and a message; the edge, the
// chain depth, the rate limit and what happens to the requester are decided here. A denied request returns the reason.
//
// Each writing function takes the Q it writes on (a transaction, when the caller needs the write to stand or fall with its own) and an
// *After that collects what must wait for the commit: the inbox file and the telling.

// TerminalStates are the states an agent may not move itself out of. STALLED is a task defect and only a human clears it (section 8.6).
var TerminalStates = map[string]bool{states.Stalled: true}

// ReviewPingpongLimit: a review that bounces on the same file this many times is not a review problem any more.
const ReviewPingpongLimit = 3

// CancelVerdict is what a cancelled request says, to the requester as its answer and to anyone who reads the row.
const CancelVerdict = "withdrawn by the founder"

// After is what a write leaves to run once it has committed, in order; each may add a line to the reply.
type After struct{ fns []func() string }

// Add queues fn.
func (a *After) Add(fn func() string) { a.fns = append(a.fns, fn) }

// Run runs what was queued and answers the lines they said, joined by newlines.
func (a *After) Run() string {
	if a == nil {
		return ""
	}
	var lines []string
	for _, fn := range a.fns {
		if s := fn(); s != "" {
			lines = append(lines, s)
		}
	}
	a.fns = nil
	return strings.Join(lines, "\n")
}

func (e *Env) isTerminal(q db.Q, agent string) bool {
	row := stateOf(q, agent)
	return row != nil && TerminalStates[row.State]
}

// ---------------------------------------------------------------- the inbox file

func (e *Env) inboxDir(target string) string {
	return filepath.Join(paths.AgentDir(e.Home, target), "inbox")
}

func inboxName(rid int64) string { return fmt.Sprintf("%06d.json", rid) }

// ForgetInbox removes the inbox file a request wrote for target, once the request is out of the queue.
func (e *Env) ForgetInbox(target string, rid int64) {
	os.Remove(filepath.Join(e.inboxDir(target), inboxName(rid)))
}

// deliver drops the request into the target's inbox: the durable record an agent reads on wake. The database row is what the daemon
// schedules from; both carry the same id so a crash between them is recoverable in either direction.
func (e *Env) deliver(rid int64, requester, target, body, files string, d team.Decision) error {
	dir := e.inboxDir(target)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	var list []string
	for _, f := range strings.Split(files, ",") {
		if f != "" {
			list = append(list, f)
		}
	}
	fl := "[]"
	if len(list) > 0 {
		items := make([]string, len(list))
		for i, f := range list {
			items[i] = "    " + rpc.PyJSON(f, false, false)
		}
		fl = "[\n" + strings.Join(items, ",\n") + "\n  ]"
	}
	opt := func(p *string) string {
		if p == nil {
			return "null"
		}
		return rpc.PyJSON(*p, false, false)
	}
	text := fmt.Sprintf("{\n  \"id\": %d,\n  \"ts\": %d,\n  \"from\": %s,\n  \"to\": %s,\n  \"relation\": %s,\n  \"kind\": %s,\n  \"response\": %s,\n  \"chain_depth\": %d,\n  \"files\": %s,\n  \"body\": %s\n}\n",
		rid, e.now(), rpc.PyJSON(requester, false, false), rpc.PyJSON(target, false, false), opt(d.Relation), opt(d.Kind),
		rpc.PyJSON(d.Response, false, false), d.ChainDepth, fl, rpc.PyJSON(body, false, false))
	return writeAtomic(filepath.Join(dir, inboxName(rid)), text)
}

func writeAtomic(path, text string) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".*.tmp")
	if err != nil {
		return err
	}
	if _, err := tmp.WriteString(text); err != nil {
		tmp.Close()
		os.Remove(tmp.Name())
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmp.Name())
		return err
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		os.Remove(tmp.Name())
		return err
	}
	return nil
}

func (e *Env) deliverAfter(after *After, rid int64, requester, target, body, files string, d team.Decision) {
	after.Add(func() string {
		if err := e.deliver(rid, requester, target, body, files, d); err != nil {
			e.log("  #%d: the inbox file for %s was not written: %v", rid, target, err)
		}
		return ""
	})
}

// ---------------------------------------------------------------- decisions and reports

func firstLineTitle(text string) string {
	first := strings.SplitN(strings.TrimSpace(text), "\n", 2)[0]
	t := textsafe.Oneline(first)
	if r := []rune(t); len(r) > 80 {
		t = string(r[:80])
	}
	if t == "" {
		return "a decision"
	}
	return t
}

func ptr(s string) *string { return &s }

// RaiseDecision puts something only a human can act on in front of them: a decision task of the founder's, opened by agent, with the
// request that shows it as its card. agent is who it is about. Fire and forget, so nothing is typed back at agent when the human reads it.
// Once per shape: exactly the same text still waiting from the same agent is that decision, not a second one.
func (e *Env) RaiseDecision(q db.Q, after *After, agent, text, reason, files string, chainID string, depth int64, parentID int64, extra ...db.KV) int64 {
	title := firstLineTitle(text)
	body := text
	if same := must(db.OpenDecisionCardIDs(q, agent, "", &body)); len(same) > 0 {
		return same[0]
	}
	rid := must(db.AddRequest(q, db.NewRequest{Requester: agent, Target: db.Human, Body: text, Files: files, Response: "fire_and_forget", Kind: "task",
		ChainDepth: depth, ParentID: parentID, ChainID: chainID}))
	tid := must(db.NewTask(q, title, db.Human, agent, 0, db.DefaultPriority, "decision", 0))
	must(db.SetTaskQueued(q, tid, db.Human))
	check(db.LinkRequest(q, tid, rid))
	e.deliverAfter(after, rid, agent, db.Human, text, files, team.Decision{Allowed: true, Reason: reason, Kind: ptr("task"), Response: "fire_and_forget", ChainDepth: int(depth)})
	payload := append([]db.KV{{K: "request_id", V: rid}, {K: "task_id", V: tid}, {K: "reason", V: reason}}, extra...)
	must(db.AddEvent(q, agent, "raise_decision", payload...))
	return rid
}

// Report tells the human something that asks nothing: a say, so nothing is typed back at agent and nothing waits on the human.
func (e *Env) Report(q db.Q, after *After, agent, text, reason string) int64 {
	rid := must(db.AddRequest(q, db.NewRequest{Requester: agent, Target: db.Human, Body: text, Response: "fire_and_forget", Kind: "say"}))
	e.deliverAfter(after, rid, agent, db.Human, text, "", team.Decision{Allowed: true, Reason: reason, Kind: ptr("say"), Response: "fire_and_forget"})
	must(db.AddEvent(q, agent, "report", db.KV{K: "request_id", V: rid}, db.KV{K: "reason", V: reason}))
	return rid
}

func (e *Env) stopRunaway(q db.Q, after *After, requester, target, body, files, chainID string, depth, parentID int64, reason string) int64 {
	text := fmt.Sprintf("[stopped] %s -> %s was stopped: %s\n\nOriginal request:\n%s", requester, target, reason, body)
	return e.RaiseDecision(q, after, requester, text, reason, files, chainID, depth, parentID, db.KV{K: "blocked_target", V: target})
}

// cycleInChain detects A->B->A inside one chain (team model section 6). A chain with the human at one end is exempt, and so is a say.
func cycleInChain(q db.Q, chainID, requester, target, kind string) string {
	if chainID == "" || requester == db.Human || target == db.Human || kind == sayKind {
		return ""
	}
	for _, r := range must(db.ChainMembers(q, chainID)) {
		if r.Requester == target && r.Target == requester {
			return fmt.Sprintf("cycle: %s already asked %s in this chain (request #%d), and this would send it straight back", target, requester, r.ID)
		}
	}
	return ""
}

func fileSet(files string) map[string]bool {
	out := map[string]bool{}
	for _, f := range strings.Split(files, ",") {
		if f != "" {
			out[f] = true
		}
	}
	return out
}

// reviewPingpong counts review round-trips on the same file within one chain.
func reviewPingpong(q db.Q, chainID, target, files string) string {
	if chainID == "" || target != "reviewer" || files == "" {
		return ""
	}
	wanted := fileSet(files)
	if len(wanted) == 0 {
		return ""
	}
	trips := 0
	for _, r := range must(db.ChainMembers(q, chainID)) {
		if r.Target != "reviewer" {
			continue
		}
		for f := range fileSet(r.Files.String) {
			if wanted[f] {
				trips++
				break
			}
		}
	}
	if trips < ReviewPingpongLimit {
		return ""
	}
	names := make([]string, 0, len(wanted))
	for f := range wanted {
		names = append(names, f)
	}
	sortStrings(names)
	return fmt.Sprintf("review ping-pong: %d rounds on %s in this chain. Two agents who cannot agree is usually a spec question.", trips, strings.Join(names, ", "))
}

// ---------------------------------------------------------------- ask and say

// AskIn is ask's arguments. Speaker is who is really typing the command: the daemon has no environment of its own and takes the caller's
// (nil: nobody the daemon can name). Notify false leaves telling the target to the caller. NoCard is `task new`'s own task.
type AskIn struct {
	Target, Body, Files, Requester string
	ParentID                       int64
	Kind                           string
	SendNow, OnBehalfOf            bool
	Speaker                        *string
	Notify                         bool
	NoCard                         bool
	// Within is more to write in the transaction that writes the row: the row and what Within writes stand or fall together.
	Within func(q db.Q, rid int64, message string) error
}

// AskOut is ask's answer. The writes are on the Q given; After holds the inbox file and the telling to run once they commit (its lines are
// appended to Message by Finish). A refusal has OK false, and its writes (a denied row, the THROTTLED state) are meant to commit.
type AskOut struct {
	OK      bool
	Message string
	RID     int64
	After   *After
	notify  bool
	target  string
}

// Finish runs what waits for the commit and gives the message ask returns.
func (o *AskOut) Finish() string {
	extra := o.After.Run()
	if extra != "" && o.OK && o.notify {
		return o.Message + "\n" + extra
	}
	return o.Message
}

func refuse(msg string) *AskOut { return &AskOut{Message: msg, After: &After{}} }

// Ask routes and, if allowed, enqueues. Returns (allowed, message, request id) in AskOut.
func (e *Env) Ask(q db.Q, in AskIn) (out *AskOut, err error) {
	defer Catch(&err)
	requester := in.Requester
	// Nobody may send as the human: that name carries the routing table's reach to every agent and exempts a chain from the loop guards.
	if requester == db.Human && in.Speaker != nil && *in.Speaker != db.Human && !in.OnBehalfOf {
		return refuse(fmt.Sprintf("%s may not ask as %s: that name is the human's, and it is exempt from the loop guards.", *in.Speaker, db.Human)), nil
	}
	if requester == "" {
		return refuse("cannot tell who is asking: the request named nobody. Agents are started with `hl launch <agent>`, which sets OCP_AGENT."), nil
	}
	t, terr := team.LoadTeam(e.Home)
	if terr != nil {
		return nil, terr
	}
	after := &After{}
	var depth int64
	chainID, isReply := "", false
	if in.ParentID != 0 {
		parent := must(db.GetRequest(q, in.ParentID))
		if parent == nil {
			return refuse(fmt.Sprintf("parent request %d does not exist", in.ParentID)), nil
		}
		depth = parent.ChainDepth + 1
		chainID = parent.ChainID.String
		isReply = RepliesTo(parent, requester, in.Target)
		if requester == db.Human { // the human's turn starts the count again, and keeps the thread
			depth = 0
		}
	}
	// A loop is stopped and raised to the founder, never silently dropped.
	for _, detect := range []string{cycleInChain(q, chainID, requester, in.Target, in.Kind), reviewPingpong(q, chainID, in.Target, in.Files)} {
		if detect != "" {
			eid := e.stopRunaway(q, after, requester, in.Target, in.Body, in.Files, chainID, depth, in.ParentID, detect)
			o := refuse(fmt.Sprintf("%s\nRaised to the founder as #%d.", detect, eid))
			o.After = after
			return o, nil
		}
	}
	d := t.Route(requester, in.Target, int(depth), isReply)
	now := e.now()
	if !d.Allowed {
		if must(db.StandingDenial(q, requester, in.Target, in.Body, in.Files, now-team.AskWindowS)) == 0 {
			kind := in.Kind
			if kind == "" {
				kind = sayKind
			}
			must(db.AddRequest(q, db.NewRequest{Requester: requester, Target: in.Target, Body: in.Body, Files: in.Files, Kind: kind, ChainDepth: depth,
				ParentID: in.ParentID, State: "denied", Verdict: d.Reason, ChainID: chainID}))
		}
		// Hitting the depth limit is a runaway, not an ordinary refusal.
		if strings.Contains(d.Reason, "chain depth") && in.Target != db.Human {
			eid := e.stopRunaway(q, after, requester, in.Target, in.Body, in.Files, chainID, depth, in.ParentID, d.Reason)
			o := refuse(fmt.Sprintf("%s\nRaised to the founder as #%d.", d.Reason, eid))
			o.After = after
			return o, nil
		}
		o := refuse(d.Reason)
		o.After = after
		return o, nil
	}
	limited, note, rerr := t.RateLimited(q, requester, e.nowT())
	if rerr != nil {
		return nil, rerr
	}
	if limited {
		window := now - team.AskWindowS
		// The first refusal keeps the text; an identical one behind it is the same refusal, not a second event.
		rid := must(db.StandingDenial(q, requester, in.Target, in.Body, in.Files, window))
		if rid == 0 {
			kind := in.Kind
			if kind == "" {
				kind = strOf(d.Kind)
			}
			if kind == "" {
				kind = sayKind
			}
			rid = must(db.AddRequest(q, db.NewRequest{Requester: requester, Target: in.Target, Body: in.Body, Files: in.Files, Relation: strOf(d.Relation),
				Response: d.Response, Kind: kind, ChainDepth: depth, ParentID: in.ParentID, State: "denied", Verdict: note, ChainID: chainID}))
		}
		freeAt := now + team.AskWindowS
		if oldest, ok := ok2(db.OldestCountedAsk(q, requester, window)); ok {
			freeAt = oldest + team.AskWindowS
		}
		// Section 8.6: too many asks is a resource problem, so it throttles and clears itself when a slot frees.
		if !e.isTerminal(q, requester) {
			check(db.SetAgentState(q, requester, states.Throttled, freeAt, note))
		}
		when := time.Unix(freeAt, 0).Format("15:04")
		o := refuse(fmt.Sprintf("%s - %s is THROTTLED. The window rolls, so the next slot frees at %s; wait for it rather than retrying, because a refusal sends nothing. Your text is kept as #%d.",
			note, requester, when, rid))
		o.After = after
		return o, nil
	}
	kind := in.Kind
	if kind == "" {
		kind = strOf(d.Kind)
	}
	rid := must(db.AddRequest(q, db.NewRequest{Requester: requester, Target: in.Target, Body: in.Body, Files: in.Files, Relation: strOf(d.Relation),
		Response: d.Response, Kind: kind, ChainDepth: depth, ParentID: in.ParentID, ChainID: chainID}))
	if in.SendNow {
		must(db.MarkSendNow(q, rid))
	}
	if !in.NoCard && in.Target == db.Human && in.Kind == "" && strOf(d.Kind) == "task" {
		// What is asked of the founder is a decision task, and this row its card; `task new` opens its own task and says NoCard.
		tid := must(db.NewTask(q, firstLineTitle(in.Body), db.Human, requester, 0, db.DefaultPriority, "decision", 0))
		must(db.SetTaskQueued(q, tid, db.Human))
		check(db.LinkRequest(q, tid, rid))
	}
	must(db.AddEvent(q, requester, "ask", db.KV{K: "request_id", V: rid}, db.KV{K: "target", V: in.Target}, db.KV{K: "relation", V: nullable(d.Relation)},
		db.KV{K: "response", V: d.Response}, db.KV{K: "chain_depth", V: depth}))
	msg := fmt.Sprintf("queued as #%d for %s (response=%s, %s)", rid, in.Target, d.Response, note)
	if in.Within != nil {
		if err := in.Within(q, rid, msg); err != nil {
			return nil, err
		}
	}
	d.ChainDepth = int(depth)
	e.deliverAfter(after, rid, requester, in.Target, in.Body, in.Files, d)
	if in.Notify {
		target := in.Target
		after.Add(func() string { return e.Push(e.DB, target, rid) })
	}
	return &AskOut{OK: true, Message: msg, RID: rid, After: after, notify: in.Notify, target: in.Target}, nil
}

func nullable(p *string) any {
	if p == nil {
		return nil
	}
	return *p
}

// SayIn is say's arguments.
type SayIn struct {
	Target, Body, Requester string
	ParentID                int64
	Speaker                 *string
	Notify                  bool
	// Within(q, message, request id) is more to write in the transaction that writes the reply and settles the request it answers.
	Within func(q db.Q, message string, rid int64) error
}

// Say is a question answered from a look-up, not new work: always ahead of the queue, never a head, and the sender never parked on
// it. ParentID replies to and settles the request it answers. The returned *Settled, when set, is for the caller to run SettledTell on
// once the writes commit.
func (e *Env) Say(q db.Q, in SayIn) (*AskOut, *Settled, error) {
	var settled *Settled
	ask := AskIn{Target: in.Target, Body: in.Body, Requester: in.Requester, ParentID: in.ParentID, Kind: sayKind, Speaker: in.Speaker, Notify: in.Notify}
	if in.ParentID != 0 || in.Within != nil {
		ask.Within = func(c db.Q, rid int64, note string) (err error) {
			defer Catch(&err)
			if in.ParentID != 0 {
				settled = e.closeReplied(c, in.ParentID, rid, in.Body)
			}
			if in.Within != nil {
				return in.Within(c, note, rid)
			}
			return nil
		}
	}
	out, err := e.Ask(q, ask)
	if err != nil || !out.OK {
		return out, nil, err
	}
	return out, settled, nil
}

// Settled is what closeReplied closed, for the tell after the commit (the arguments of afterClose).
type Settled struct {
	RID    int64
	Row    *db.Request
	State  string
	Notify bool
}

// SettledTell runs the tell of a request a reply settled.
func (e *Env) SettledTell(q db.Q, s *Settled) (string, error) {
	if s == nil {
		return "", nil
	}
	return e.AfterClose(q, s.RID, s.Row, s.State, s.Notify)
}

// closeReplied: a reply settles the request it answers, silently - the reply is what the asker is told, and it is marked as the
// answer. Only when it comes from the request's own target, to its requester, and the request is no roster proposal and no task.
func (e *Env) closeReplied(q db.Q, parentID, replyID int64, replyBody string) *Settled {
	parent, reply := must(db.GetRequest(q, parentID)), must(db.GetRequest(q, replyID))
	proposal := parent != nil && e.Proposals != nil && e.Proposals.OpenProposalOf(parentID) != ""
	if parent != nil && parent.State == "queued" && !proposal && parent.Kind.String != "task" &&
		reply != nil && reply.Requester == parent.Target && reply.Target == parent.Requester &&
		must(db.CloseIfQueued(q, parentID, "done", replyBody, db.CloseOpts{Announced: true, AnsweredBy: replyID})) {
		return &Settled{parentID, parent, "done", false}
	}
	return nil
}
