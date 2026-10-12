package supervisor

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
)

// `ask` and `say` as the daemon's own verbs (daemon/sends.py): the daemon runs the same checks and the same Ask/Say the CLI used to, writes the
// row, returns its id at once, and then - on a goroutine of its own, so the caller never waits for a wake - types the notice. Who is asking
// travels in the call (the requester, and the speaker, the caller's own name): the daemon has no environment to read it from.

const noWho = "cannot tell who is asking: the request named nobody. Agents are started with `hl launch <agent>`, which sets OCP_AGENT."

// Handed is what a push the daemon took over answers.
const Handed = "handed to the daemon; it is typed as soon as the agent can take it"

// tell is what follows the reply: a task keeps the one-at-a-time head rule (PushHere), everything else is typed at once (TellNow).
func (e *Env) tell(rid int64, target string) {
	var out string
	defer e.guard(fmt.Sprintf("telling %s about #%d", target, rid), &out)
	q := db.Q(e.DB)
	row := must(db.GetRequest(q, rid))
	if row == nil || row.State != "queued" {
		return
	}
	if row.Kind.String == "task" {
		e.PushHere(q, target, 0)
	} else {
		must(e.TellNow(q, rid))
	}
}

// RunInline makes the telling run before the verb returns, so a test sees its effect; the daemon never does.
var RunInline = false

func (e *Env) background(name string, fn func()) {
	run := func() {
		var out string
		defer e.guard(name, &out)
		fn()
	}
	if RunInline {
		run()
		return
	}
	go run()
}

func (e *Env) after(rid int64, target string) {
	if rid == 0 || target == db.Human {
		return
	}
	e.background(fmt.Sprintf("telling %s about #%d", target, rid), func() { e.tell(rid, target) })
}

// shellQuote is shlex.quote.
func shellQuote(s string) string {
	if s == "" {
		return "''"
	}
	safe := true
	for _, c := range s {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.ContainsRune("@%+=:,./-_", c)) {
			safe = false
			break
		}
	}
	if safe {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'"
}

// isReportOf: is target one of me's reports, by the supervisor edge in team.yaml? An unreadable table refuses nothing here.
func (e *Env) isReportOf(me, target string) bool {
	t, err := team.LoadTeam(e.Home)
	return err == nil && t.SupervisorOf(target) == me
}

// sayCmd is a `hl say` line ready to paste: the body as sent, quoted, with the flags that would have worked.
func sayCmd(target, body string, parent int64, flags ...string) string {
	parts := []string{"hl say", target, shellQuote(body)}
	if parent != 0 {
		parts = append(parts, fmt.Sprintf("--parent %d", parent))
	}
	return strings.Join(append(parts, flags...), " ")
}

// workingSay is the line to suggest for a say to target the checks refused: keep a --task the target holds; to a report with no task, the task
// that would reach it; else the plain say.
func (e *Env) workingSay(q db.Q, me, target, body string, parent, task int64) string {
	if task != 0 {
		if row := must(db.GetTask(q, task)); row != nil && row.Owner == target {
			return sayCmd(target, body, parent, fmt.Sprintf("--task T%d", task))
		}
	}
	if parent == 0 && e.isReportOf(me, target) {
		return fmt.Sprintf("hl task new --assign %s -t 2h '<title>' %s", target, shellQuote(body))
	}
	return sayCmd(target, body, parent)
}

// SayVerbIn is the `say` verb's arguments. Task 0 is none; NoTask nil is not given.
type SayVerbIn struct {
	Requester string
	Speaker   *string
	Target    string
	Body      string
	Task      int64
	NoTask    *string
	Parent    int64
	// Effect is the daemon's record of the write, made in the transaction that writes the message and what is written with it.
	Effect func(q db.Q, message string, rid int64) error
}

// SayVerbOut is the verb's answer; After runs once the writes have committed (the telling, the settled request's tell, the memo holder's say).
type SayVerbOut struct {
	OK    bool
	Msg   string
	After func()
}

// SayVerb is `hl say TARGET BODY`: a message that needs no answer, typed at once.
func (e *Env) SayVerb(q db.Q, in SayVerbIn) (out *SayVerbOut, err error) {
	defer Catch(&err)
	fail := func(msg string) (*SayVerbOut, error) { return &SayVerbOut{Msg: msg, After: func() {}}, nil }
	if in.Requester == "" {
		return fail(noWho)
	}
	me, target, task, body := in.Requester, in.Target, in.Task, in.Body
	var parent *db.Request
	if in.Parent != 0 {
		parent = must(db.GetRequest(q, in.Parent))
	}
	effect := func(c db.Q, note string, rid int64) error {
		if in.Effect != nil {
			return in.Effect(c, note, rid)
		}
		return nil
	}
	// A reply to the founder's own open request says what it becomes, as resolve did.
	founders := parent != nil && parent.Requester == db.Human && parent.State == "queued" && parent.Target == me && target == db.Human && parent.Kind.String != "task"
	if founders {
		var askedOn []int64
		if parent.Kind.String == "say" { // an answer to the founder's question on a task is no work to name: it joins that task
			for _, t := range must(db.TasksForRequest(q, parent.ID)) {
				askedOn = append(askedOn, t.ID)
			}
		}
		plain := len(askedOn) > 0 && task == 0 && in.NoTask == nil
		defaulted := false
		mine := func() []int64 {
			var ids []int64
			for _, t := range must(db.OpenTasks(q)) {
				if t.Owner == me {
					ids = append(ids, t.ID)
				}
			}
			return ids
		}
		if !plain && task == 0 && in.NoTask == nil {
			// Exactly one open task of the sender's is what the reply is about; with none or several the choice is theirs.
			if m := mine(); len(m) == 1 {
				task, defaulted = m[0], true
			}
		}
		var tasks []int64
		if task != 0 {
			tasks = []int64{task}
		}
		if !plain {
			if refusal := e.checkHumanResolve(q, parent, tasks, in.NoTask, me, true); refusal != "" {
				m := mine()
				if len(m) > 3 {
					m = m[:3]
				}
				var tries []string
				for _, t := range m {
					tries = append(tries, sayCmd(target, body, in.Parent, fmt.Sprintf("--task T%d", t)))
				}
				tries = append(tries, sayCmd(target, body, in.Parent, "--no-task '<why>'"))
				return fail(refusal + "\nTry:\n  " + strings.Join(tries, "\n  "))
			}
		}
		within := func(c db.Q, note string, rid int64) error {
			var err error
			func() {
				defer Catch(&err)
				if plain && rid != 0 {
					for _, tid := range askedOn {
						check(db.LinkRequest(c, tid, rid))
					}
				} else if must(db.GetRequest(c, in.Parent)).State != "queued" { // it settled, not lost a race
					e.recordHumanResolve(c, in.Parent, tasks, in.NoTask, me)
				}
			}()
			if err != nil {
				return err
			}
			return effect(c, note, rid)
		}
		res, settled, serr := e.Say(q, SayIn{Target: target, Body: body, Requester: me, ParentID: in.Parent, Speaker: in.Speaker, Within: within})
		if serr != nil {
			return nil, serr
		}
		msg := res.Message
		if res.OK && defaulted {
			msg += fmt.Sprintf("\nattached to T%d, your only open task", task)
		}
		return &SayVerbOut{OK: res.OK, Msg: msg, After: func() {
			res.After.Run()
			e.settledAfter(settled)
			if res.OK {
				e.after(res.RID, target)
			}
		}}, nil
	}
	if in.NoTask != nil && target != db.Human {
		return fail("--no-task is for replying to the founder (hl say human ...); to anyone else it means nothing. Try:\n  " + e.workingSay(q, me, target, body, in.Parent, task))
	}
	if task == 0 && in.Parent == 0 && e.isReportOf(me, target) {
		return fail("work cannot reach a report by message: make it a task (`hl task new`), or say --task T<n> to memo one. Try:\n  " +
			fmt.Sprintf("hl task new --assign %s -t 2h '<title>' %s", target, shellQuote(body)))
	}
	if task != 0 {
		// Structural, not read from the text: a say to a report about a task is that task's memo, delivered as one.
		row := must(db.GetTask(q, task))
		if row == nil {
			return fail(fmt.Sprintf("no task T%d. Try:\n  %s", task, e.workingSay(q, me, target, body, in.Parent, 0)))
		}
		if target == db.Human && row.OpenedBy == me {
			// A reply to the founder naming the task it became: it is theirs to read, not a memo, and the task is named whoever holds it now.
			within := func(c db.Q, note string, rid int64) error {
				if err := db.LinkRequest(c, task, rid); err != nil {
					return err
				}
				return effect(c, note, rid)
			}
			res, settled, serr := e.Say(q, SayIn{Target: target, Body: body, Requester: me, ParentID: in.Parent, Speaker: in.Speaker, Within: within})
			if serr != nil {
				return nil, serr
			}
			return &SayVerbOut{OK: res.OK, Msg: res.Message, After: func() {
				res.After.Run()
				e.settledAfter(settled)
				if res.OK && res.RID != 0 {
					e.after(res.RID, target)
				}
			}}, nil
		}
		if row.Owner != target {
			return fail(fmt.Sprintf("T%d is with %s, not %s. Try:\n  %s", task, row.Owner, target, sayCmd(row.Owner, body, in.Parent, fmt.Sprintf("--task T%d", task))))
		}
		ok, msg, _, merr := e.MemoAdd(q, task, me, body)
		if merr != nil {
			return nil, merr
		}
		if ok && in.Effect != nil {
			if err := in.Effect(q, msg, 0); err != nil {
				return nil, err
			}
		}
		return &SayVerbOut{OK: ok, Msg: msg, After: func() {
			if ok {
				if err := e.TellMemoHolder(e.DB, task, me, "added", body); err != nil {
					e.log("  telling the holder of T%d about the memo failed: %v", task, err)
				}
			}
		}}, nil
	}
	var within func(db.Q, string, int64) error
	if in.Effect != nil {
		within = in.Effect
	}
	res, settled, serr := e.Say(q, SayIn{Target: target, Body: body, Requester: me, ParentID: in.Parent, Speaker: in.Speaker, Within: within})
	if serr != nil {
		return nil, serr
	}
	return &SayVerbOut{OK: res.OK, Msg: res.Message, After: func() {
		res.After.Run()
		e.settledAfter(settled)
		if res.OK {
			e.after(res.RID, target)
		}
	}}, nil
}

func (e *Env) settledAfter(s *Settled) {
	if s == nil {
		return
	}
	var out string
	defer e.guard("telling the asker of a settled request", &out)
	if _, err := e.SettledTell(e.DB, s); err != nil {
		e.log("  telling the asker of #%d failed: %v", s.RID, err)
	}
}

// PushCheck is why target cannot be pushed to, "" for none.
func (e *Env) PushCheck(target string) string {
	if target == db.Human || !e.registered(target) {
		return target + " is not an agent"
	}
	return ""
}

// PushAct tells target about what was queued for it: request rid when one is named (a message is typed at once, a task keeps the one-at-a-time
// rule), else its next task.
func (e *Env) PushAct(target string, rid int64) string {
	if rid != 0 {
		e.after(rid, target)
	} else {
		e.background("push-"+target, func() { e.PushHere(e.DB, target, 0) })
	}
	return Handed
}

// PushVerb is the `push` verb: (ok, message).
func (e *Env) PushVerb(target string, rid int64) (bool, string) {
	if why := e.PushCheck(target); why != "" {
		return false, why
	}
	return true, e.PushAct(target, rid)
}

// PushAnswerCheck is why an answer cannot be pushed to requester, "" for none.
func (e *Env) PushAnswerCheck(requester string) string {
	if requester == db.Human || !e.registered(requester) {
		return requester + " is not an agent"
	}
	return ""
}

// PushAnswerAct puts the answer in front of the agent that asked, on a goroutine of its own.
func (e *Env) PushAnswerAct(requester string) string {
	e.background("push-answer-"+requester, func() { e.PushAnswerHere(e.DB, requester) })
	return Handed
}

// PushAnswerVerb is the `push.answer` verb: (ok, message).
func (e *Env) PushAnswerVerb(requester string) (bool, string) {
	if why := e.PushAnswerCheck(requester); why != "" {
		return false, why
	}
	return true, e.PushAnswerAct(requester)
}

// NotifyCancelledCheck is the request NotifyCancelledVerb would tell about and why not: (row, "") or (nil, why). Pure reads.
func (e *Env) NotifyCancelledCheck(q db.Q, rid int64) (row *db.Request, why string, err error) {
	defer Catch(&err)
	row = must(db.GetRequest(q, rid))
	if row == nil {
		return nil, fmt.Sprintf("no request #%d", rid), nil
	}
	if row.Target == db.Human || !e.registered(row.Target) {
		return nil, row.Target + " is not an agent", nil
	}
	if row.State != "landed" || !row.DeliveredAt.Valid || !must(db.ClosedUnder(q, row.ID)) {
		return nil, fmt.Sprintf("#%d was not cancelled after it was delivered", row.ID), nil
	}
	return row, "", nil
}

// NotifyCancelledAct types the cancel at the agent on a goroutine of its own.
func (e *Env) NotifyCancelledAct(row *db.Request) string {
	e.background(fmt.Sprintf("notify-cancelled-%d", row.ID), func() {
		if _, _, err := e.NotifyCancelled(e.DB, row, shownVerdict(row)); err != nil {
			e.log("  notify-cancelled-%d failed: %v", row.ID, err)
		}
	})
	return Handed
}

// NotifyCancelledVerb tells an agent a request it was handed was closed under it. Only a delivered request already closed as rejected, and only
// with the verdict on its row: what the daemon types is never the caller's words.
func (e *Env) NotifyCancelledVerb(q db.Q, rid int64) (bool, string, error) {
	row, why, err := e.NotifyCancelledCheck(q, rid)
	if err != nil || why != "" {
		return false, why, err
	}
	return true, e.NotifyCancelledAct(row), nil
}

// CheckHumanResolve is the refusal for resolving a request the human made without saying what it becomes; "" when it may go ahead. Any --task
// id is checked on every row, before anything is written. The guard is on the answer, not on the ask: agent-to-agent rows are conversation and
// never reach it. The founder's own hands pass.
func (e *Env) checkHumanResolve(q db.Q, row *db.Request, tasks []int64, noTask *string, me string, oneTask bool) string {
	for _, tid := range tasks {
		t := must(db.GetTask(q, tid))
		if t == nil {
			return fmt.Sprintf("no task T%d", tid)
		}
		if t.Ended() {
			word := "done"
			if t.DiscardedBy.String != "" {
				word = "discarded"
			}
			return fmt.Sprintf("task T%d is already %s", tid, word)
		}
		if me != "" && me != db.Human && me != t.Owner && me != t.OpenedBy {
			return fmt.Sprintf("T%d is not yours: only a task you hold, or opened, can be named here (it is %s's)", tid, t.Owner)
		}
	}
	if row.Requester != db.Human || me == db.Human {
		return ""
	}
	given := noTask != nil && *noTask != ""
	if (len(tasks) > 0) == given {
		names := "--task T<n> [T<n> ...]"
		if oneTask {
			names = "--task T<n>"
		}
		return fmt.Sprintf("#%d is the founder's: say whether work follows it. %s names the tasks it opened (one per deliverable promised), "+
			"--no-task 'reason' says nothing needs building. `hl task new --assign NAME 'title' -t 2h` makes a task.", row.ID, names)
	}
	return ""
}

// RecordHumanResolve is what a guarded close leaves behind: the links (--task), or the reason nothing was built (--no-task).
func (e *Env) recordHumanResolve(q db.Q, rid int64, tasks []int64, noTask *string, me string) {
	for _, tid := range tasks {
		check(db.LinkRequest(q, tid, rid))
	}
	if noTask != nil && *noTask != "" {
		must(db.AddEvent(q, me, "no_task", db.KV{K: "request_id", V: rid}, db.KV{K: "reason", V: *noTask}))
	}
}
