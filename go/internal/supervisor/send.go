package supervisor

import (
	"crypto/sha1"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/timefmt"
)

// Seeing and typing at an agent's session (daemon/delivery.py, "sending").

const (
	// SeenFreshS is how old the sweep's reading may be before a send looks again. A tick is 2s, so anything older came from another tick.
	SeenFreshS = 10
	// DrawnFreshS is how long one screen read's answer holds.
	DrawnFreshS = 5
	// ExitDialogWaitS is how long after typing /exit to look for the dialog it may raise.
	ExitDialogWaitS = 6
	EndPollS        = 1
	// DialogStall begins the detail of an agent stalled on a dialog ocp cannot get past.
	DialogStall = "cannot get past a dialog"
	// TypeSettleS: a destructive type (a handoff's /clear or /exit) may not follow any type at the same agent within this long.
	TypeSettleS = 15
	// UnsentGraceS: a send that has only just landed is still being drawn.
	UnsentGraceS = 3
	// UnsentEnterWaitS: an Enter that has had this long and left the text there did not take.
	UnsentEnterWaitS = 10
	// MaxTypedLines is the most lines of text typed at an agent: Claude Code wraps a longer paste in <pasted_content>.
	MaxTypedLines = 3
	// ClearWaitS: a clear that background work holds off is owed, and typed when the work ends or at the latest this long after.
	ClearWaitS = 300
)

// ClearNotice is told once to an agent whose clear background work holds off.
const ClearNotice = "ocp: your context is full (%s), so this session will be cleared in %d minutes. " +
	"No reply is needed. Before then: 1. Stop your background shells. Anything still running at " +
	"that time is cut off. 2. If you were waiting on a result, write what was running and what is " +
	"left in: hl progress \"done: ... left: ...\" - the next session starts from that note. " +
	"3. Do not start new work or new background jobs. When your background work has ended, the " +
	"clear happens at once."

// TableListsSession is what the process table says: yes if it lists a session for the agent, no if not; known false when it could not be read.
func (e *Env) TableListsSession(agent string) (yes, known bool) {
	e.init()
	e.mu.Lock()
	o, have := e.seen[agent]
	fresh := have && e.nowT().Sub(e.seenAt) <= SeenFreshS*time.Second
	e.mu.Unlock()
	if fresh {
		if !o.SessionSeen {
			return false, false
		}
		return o.HasPID, true
	}
	sessions, ok := e.Observer.LiveSessions()
	if !ok {
		return false, false
	}
	return sessions[agent], true
}

// ScreenDrawsSession is whether the agent's terminal draws a session right now: the screen's word, held DrawnFreshS.
func (e *Env) ScreenDrawsSession(agent string) bool {
	e.init()
	e.mu.Lock()
	hit, have := e.drawn[agent]
	e.mu.Unlock()
	if have && e.nowT().Sub(hit.at) <= DrawnFreshS*time.Second {
		return hit.yes
	}
	handle := must(db.Terminal(e.DB, agent))
	answer := handle != "" && e.Terminal.Available() && e.SessionStillDrawn(handle, false)
	e.mu.Lock()
	e.drawn[agent] = drawnAt{e.nowT(), answer}
	e.mu.Unlock()
	return answer
}

// SessionPresent is whether there is a session to type into, or only the shell it left behind. Fail-closed, with two witnesses: a
// process the table lists, or a session drawn on the agent's terminal. An unreadable table with nothing drawn is a no.
func (e *Env) SessionPresent(agent string) bool {
	if yes, _ := e.TableListsSession(agent); yes {
		return true
	}
	return e.ScreenDrawsSession(agent)
}

// WaitSessionGone is whether the agent's session is out of a fresh reading of the process table, within EndWaitS. An unreadable table is a no.
func (e *Env) WaitSessionGone(agent string) bool {
	deadline := e.nowT().Add(EndWaitS * time.Second)
	for {
		sessions, ok := e.Observer.LiveSessions()
		if ok && !sessions[agent] {
			e.mu.Lock()
			delete(e.seen, agent)
			e.mu.Unlock()
			return true
		}
		if !e.nowT().Before(deadline) {
			return false
		}
		e.sleep(EndPollS * time.Second)
	}
}

// SessionStillDrawn is whether an agent's session is still drawn on this terminal's screen; with wait the screen is given EndWaitS to
// clear. A screen that cannot be read is not a yes.
func (e *Env) SessionStillDrawn(handle string, wait bool) bool {
	if handle == "" {
		return false
	}
	deadline := e.nowT()
	if wait {
		deadline = deadline.Add(EndWaitS * time.Second)
	}
	for {
		if session, known := e.Terminal.SessionOnScreen(handle); !session || !known {
			return false
		}
		if !e.nowT().Before(deadline) {
			return true
		}
		e.sleep(EndPollS * time.Second)
	}
}

// WaitForSession is whether the launch typed started a session, within timeoutS. With a prompt file, the session counts only once that
// file is gone.
func (e *Env) WaitForSession(agent string, timeoutS int, prompt string) bool {
	deadline := e.nowT().Add(time.Duration(timeoutS) * time.Second)
	for {
		sessions, ok := e.Observer.LiveSessions()
		if ok && sessions[agent] && !(prompt != "" && fileExists(prompt)) {
			return true
		}
		if !e.nowT().Before(deadline) {
			return false
		}
		e.sleep(time.Second)
	}
}

func fileExists(p string) bool { _, err := os.Stat(p); return err == nil }

// pastDialog gets past a dialog before typing an exit or a clear: "" when nothing stands in the way, else why. While a reason stands,
// when the dialog first proved unpassable is kept by its title.
func (e *Env) pastDialog(q db.Q, agent string, onlyLimit bool) string {
	if held := e.Channel.HeldByOther(q, agent); held != "" {
		return e.Channel.HeldWhy(agent, held)
	}
	why, title := e.Dialogs.PastDialog(q, agent, onlyLimit)
	sinceKey, titleKey := "dialog_since:"+agent, "dialog_title:"+agent
	if why == "" {
		check(db.ClearCursor(q, sinceKey))
		check(db.ClearCursor(q, titleKey))
		return ""
	}
	// A dialog whose title did not parse is never the same as the last: its clock restarts, which errs toward not stalling.
	var mark int64
	if title != "" {
		sum := sha1.Sum([]byte(title))
		mark = int64(binary.BigEndian.Uint32(sum[:4]))
	}
	if mark == 0 || must(db.Cursor(q, titleKey)) != mark || must(db.Cursor(q, sinceKey)) == 0 {
		check(db.SetCursor(q, titleKey, mark))
		check(db.SetCursor(q, sinceKey, e.now()))
	}
	return why
}

// StalledOnDialog: a dialog ocp cannot get past, standing as long as a block takes to be raised with the founder, stops the agent.
func (e *Env) StalledOnDialog(q db.Q, agent, why string) bool {
	since := must(db.Cursor(q, "dialog_since:"+agent))
	if since == 0 || e.now()-since < e.Dialogs.DecisionAfterS() {
		return false
	}
	check(db.SetAgentState(q, agent, states.Stalled, 0, DialogStall+": "+why))
	e.log("  %s: STALLED - %s: %s", agent, DialogStall, why)
	return true
}

// ReclaimContext clears the agent's session so a handoff costs it its context. The caller holds the agent's terminal.
func (e *Env) ReclaimContext(q db.Q, agent string) (bool, string) {
	handle := must(db.Terminal(q, agent))
	if handle == "" {
		return false, "no terminal recorded; start the agent with `hl spawn` or `hl launch` inside an Orca terminal so its context can be reclaimed"
	}
	if !e.Terminal.Available() {
		return false, e.Terminal.Missing()
	}
	if !e.SessionPresent(agent) {
		return false, fmt.Sprintf("no session process; not typing %s at a shell prompt", e.Terminal.ClearCommand(handle))
	}
	if e.pastDialog(q, agent, false) != "" {
		return false, fmt.Sprintf("a dialog is open on its screen that ocp cannot get past; not typing %s at it", e.Terminal.ClearCommand(handle))
	}
	if running, _ := e.Terminal.BackgroundWork(handle); running != "" {
		return e.clearOverBackground(q, agent, handle, running)
	}
	return e.clearNow(q, agent, handle)
}

// ClearOwed is when the agent was told its clear is coming, 0 when none is owed.
func (e *Env) ClearOwed(q db.Q, agent string) int64 { return must(db.Cursor(q, "clear_owed:"+agent)) }

// ClearHold: nothing new is typed at an agent whose clear is owed.
func (e *Env) ClearHold(q db.Q, agent string) string {
	if e.ClearOwed(q, agent) != 0 {
		return agent + " is about to be cleared; nothing is typed at it until then"
	}
	return ""
}

// TypingHold is why nothing may be typed at this agent's session now: someone holds its input, the wall, or a clear owed.
func (e *Env) TypingHold(q db.Q, agent string) string {
	if held := e.Channel.HeldByOther(q, agent); held != "" {
		return e.Channel.HeldWhy(agent, held)
	}
	if w := e.WallReason(q, agent, nil); w != "" {
		return w
	}
	return e.ClearHold(q, agent)
}

func (e *Env) cacheLapsed(q db.Q, agent string, now int64) bool {
	last, ok := ok2(db.LastActivity(q, agent))
	return ok && now-last >= states.PromptCacheS
}

func (e *Env) clearNow(q db.Q, agent, handle string) (bool, string) {
	ok, why := e.Terminal.ClearContext(handle)
	e.log("  %s: %s", agent, why)
	if ok {
		check(db.ClearCursor(q, "clear_owed:"+agent))
	}
	return ok, why
}

// clearOverBackground: a clear with work still running behind the prompt goes at once when the cache has lapsed; otherwise the agent
// is told, and the clear waits for its work to end or for ClearWaitS. False here is "not yet".
func (e *Env) clearOverBackground(q db.Q, agent, handle, running string) (bool, string) {
	now := e.now()
	since := e.ClearOwed(q, agent)
	if e.cacheLapsed(q, agent, now) {
		e.log("  %s: background work is running (%s) but its cache has lapsed - clearing", agent, running)
		return e.clearNow(q, agent, handle)
	}
	owed := fmt.Sprintf("background work is running (%s); %s is owed", running, e.Terminal.ClearCommand(handle))
	if since == 0 {
		used, limit := e.Sessions.ContextUse(q, agent)
		usedText := "?"
		if used >= 0 {
			usedText = commas(used)
		}
		ok, err := e.Terminal.SendToSession(handle, fmt.Sprintf(ClearNotice, usedText+" of "+commas(limit), ClearWaitS/60))
		check(db.SetCursor(q, "clear_owed:"+agent, now)) // owed even if the notice did not go
		failed := ""
		if !ok {
			failed = " (the notice failed: " + cut(err, 80) + ")"
		}
		e.log("  %s: background work is running (%s); told it will be cleared in %d min%s", agent, running, ClearWaitS/60, failed)
		return false, owed
	}
	if now-since < ClearWaitS {
		return false, owed
	}
	e.log("  %s: %d min are up with background work still running (%s) - clearing", agent, ClearWaitS/60, running)
	ok, why := e.clearNow(q, agent, handle)
	if ok {
		// Only once it has landed: a clear that waits for the agent's turn to end is tried again next tick.
		rec := must(db.GetHandoff(q, agent))
		check(db.SetHandoff(q, agent, fmt.Sprintf("%s\n\n## Cut off by the clear\nThis session was cleared with background work still running (%s); it was told %s earlier and asked to stop it. That work is gone - rerun it if it still matters.\n",
			rec, running, timefmt.Ago(now-since))))
	}
	return ok, why
}

// WallReason is why typing at this agent's open session would only spend quota, "" for none. Checked where the keystrokes are sent.
func (e *Env) WallReason(q db.Q, agent string, row *db.AgentState) string {
	if e.Waits.AtLimit(q, agent) {
		return agent + " is at its usage limit"
	}
	if row == nil {
		row = stateOf(q, agent)
	}
	if row != nil && row.State == states.Parked && !row.ClosedAt.Valid {
		if kind := must(db.LastHandoffTrigger(q, agent)); kind != "" && e.Sessions.MovableTrigger(kind) {
			return agent + " is parked until its usage window rolls over"
		}
	}
	return ""
}

func (e *Env) liveSession(q db.Q, agent string) string { return e.Sessions.LiveSession(q, agent) }

// SessionChanged is whether the session on screen is a different one from the one the head was typed into. An id that cannot be read
// is not a difference.
func (e *Env) SessionChanged(q db.Q, agent string) bool {
	live := e.liveSession(q, agent)
	return live != "" && live != must(db.NotifiedSession(q, agent))
}

// typeGate is the re-read every path that types at an agent's terminal must do once its lock is held: "" means clear to type,
// else why not. requireSettled is for a destructive type, or one that ends a session.
func (e *Env) typeGate(q db.Q, agent, handle string, requireIdle, requireSettled bool) string {
	if held := e.Channel.HeldByOther(q, agent); held != "" {
		return e.Channel.HeldWhy(agent, held)
	}
	if requireSettled {
		if last := must(db.Cursor(q, "typed_at:"+agent)); last != 0 && e.now()-last < TypeSettleS {
			return agent + " was typed at moments ago; letting the screen catch up"
		}
	}
	if requireIdle && handle != "" && e.Terminal.Available() && !e.Terminal.TypeableNow(handle) {
		return agent + " is mid-turn or has a dialog open"
	}
	if handle != "" && e.Terminal.Available() && e.ownTextInBox(q, agent, handle) {
		return agent + " has text we typed still unsent in its input box"
	}
	return ""
}

// TypeGate is typeGate for the paths outside this package (handoff, close).
func (e *Env) TypeGate(q db.Q, agent, handle string, requireIdle, requireSettled bool) (why string, err error) {
	defer Catch(&err)
	return e.typeGate(q, agent, handle, requireIdle, requireSettled), nil
}

type unsentRec struct {
	Text    string `json:"text"`
	At      int64  `json:"at"`
	Enters  int    `json:"enters"`
	Retyped bool   `json:"retyped"`
	Blocked bool   `json:"blocked"`
}

func unsentKey(agent string) string { return "unsent:" + agent }

func (e *Env) unsentRecord(q db.Q, agent string) *unsentRec {
	raw, found := ok2(db.Meta(q, unsentKey(agent)))
	if !found || raw == "" {
		return nil
	}
	var m map[string]any
	if json.Unmarshal([]byte(raw), &m) != nil {
		return nil
	}
	if _, ok := m["text"].(string); !ok {
		return nil
	}
	var rec unsentRec
	if json.Unmarshal([]byte(raw), &rec) != nil {
		return nil
	}
	return &rec
}

func (e *Env) ownTextInBox(q db.Q, agent, handle string) bool {
	rec := e.unsentRecord(q, agent)
	if rec == nil {
		return false
	}
	box, known := e.Terminal.ComposerOnScreen(handle)
	return known && e.Terminal.IsTextTyped(box, rec.Text)
}

func (e *Env) saveUnsent(q db.Q, agent string, rec *unsentRec) {
	check(db.SetMeta(q, unsentKey(agent), pyUnsent(rec)))
}

// pyUnsent writes the record the way Python's json.dumps does (", " and ": "), which the Python daemon reads back.
func pyUnsent(r *unsentRec) string {
	yes := map[bool]string{true: "true", false: "false"}
	return fmt.Sprintf(`{"text": %s, "at": %d, "enters": %d, "retyped": %s, "blocked": %s}`,
		rpc.PyJSON(r.Text, false, true), r.At, r.Enters, yes[r.Retyped], yes[r.Blocked])
}

// RecoverUnsent presses Enter on text we typed that is still in the agent's box; true while it is (the tick types nothing else at it).
func (e *Env) RecoverUnsent(q db.Q, agent string) (held bool, err error) {
	defer Catch(&err)
	rec := e.unsentRecord(q, agent)
	handle := must(db.Terminal(q, agent))
	if rec == nil || handle == "" || !e.Terminal.Available() {
		return false, nil
	}
	if !e.ownTextInBox(q, agent, handle) {
		if _, known := e.Terminal.ComposerOnScreen(handle); known { // unreadable keeps the record
			check(db.ClearMeta(q, unsentKey(agent)))
			e.NoteHold(agent, "", false)
		}
		return false, nil
	}
	now := e.now()
	age := now - rec.At
	if rec.Blocked {
		e.NoteHold(agent, "text we typed is still unsent in its input box and Enter does not send it", false)
		return true, nil
	}
	if (rec.Enters != 0 && age < UnsentEnterWaitS) || age < UnsentGraceS {
		return true, nil
	}
	if e.Terminal.ScreenBlocker(handle) != nil {
		return true, nil // an open dialog would take the Enter
	}
	e.withTerminal(agent, func() {
		if e.TypingHold(q, agent) != "" || !e.ownTextInBox(q, agent, handle) {
			return
		}
		switch {
		case rec.Enters == 0:
			// Enter is safe in a turn: it only queues the text. Ctrl-C and a retype are not.
			e.Terminal.Send(handle, "", true)
			e.log("  %s: our text was left unsent in its input box; pressed Enter", agent)
			rec.Enters, rec.At = 1, now
		case !e.Terminal.TypeableNow(handle):
			return // Ctrl-C would cancel a running turn: wait for it to end
		case !rec.Retyped:
			e.Terminal.SendCtrlC(handle)
			if box, known := e.Terminal.ComposerOnScreen(handle); !known || box != "" {
				rec.Blocked = true // the box did not clear: typing again would append
				e.log("  %s: Enter did not send our text and the box would not clear; giving up", agent)
			} else {
				e.send(handle, rec.Text)
				e.log("  %s: Enter did not send our text; cleared the box and typed it again", agent)
				rec.Enters, rec.At, rec.Retyped = 0, now, true
			}
		default:
			rec.Blocked = true
			e.log("  %s: our text is still unsent after a second try; giving up", agent)
		}
		if rec.Blocked {
			must(db.AddEvent(q, agent, "unsent_input_blocked"))
		}
		e.mu.Lock()
		delete(e.typedText, handle)
		e.mu.Unlock()
		e.saveUnsent(q, agent, rec)
	})
	return true, nil
}

// MarkTyped is called after every successful type at the agent's terminal: the other half of typeGate's settle window. by names the
// person whose control call it was ("" for the daemon typing).
func (e *Env) MarkTyped(q db.Q, agent string, by map[string]any) {
	check(db.SetCursor(q, "typed_at:"+agent, e.now()))
	handle := must(db.Terminal(q, agent))
	e.mu.Lock()
	text, had := e.typedText[handle]
	delete(e.typedText, handle)
	e.mu.Unlock()
	if had {
		e.saveUnsent(q, agent, &unsentRec{Text: text, At: e.now()})
	}
	kind, who := "daemon_typed", map[string]any{"kind": "daemon", "name": "daemon"}
	if by != nil {
		kind, who = "control", by
	}
	e.Channel.Marker(q, agent, kind, who)
}

// send is term.send for text an agent is meant to act on: never more than MaxTypedLines lines. Past that the middle lines are joined.
func (e *Env) send(handle, text string) (bool, string) {
	lines := strings.Split(text, "\n")
	if len(lines) > MaxTypedLines {
		keep := MaxTypedLines - 2
		joined := strings.Join(lines[keep:len(lines)-1], " ")
		lines = append(append(append([]string{}, lines[:keep]...), joined), lines[len(lines)-1])
	}
	text = strings.Join(lines, "\n")
	ok, out := e.Terminal.SendToSession(handle, text)
	if ok {
		e.init()
		e.mu.Lock()
		e.typedText[handle] = text
		e.mu.Unlock()
	}
	return ok, out
}

// TypeNotice types one line at an agent's open session if it may be typed at now: not at the wall, a live session on a live terminal,
// nothing on screen a line would answer, the terminal lock held and the screen read again. False for any of them, or a failed send.
func (e *Env) TypeNotice(q db.Q, agent, text string) (sent bool, err error) {
	defer Catch(&err)
	handle := must(db.Terminal(q, agent))
	if e.TypingHold(q, agent) != "" || handle == "" || !e.Terminal.Available() || !e.Terminal.Alive(handle) ||
		!e.SessionPresent(agent) || !e.Terminal.TypeableNow(handle) {
		return false, nil
	}
	e.withTerminal(agent, func() {
		if e.typeGate(q, agent, handle, true, false) != "" {
			return
		}
		if sent, _ = e.send(handle, text); sent {
			e.MarkTyped(q, agent, nil)
		}
	})
	return sent, nil
}

// NoteFailure remembers why the agent's wake or start failed this tick; "" forgets it.
func (e *Env) NoteFailure(agent, why string) {
	e.init()
	e.mu.Lock()
	defer e.mu.Unlock()
	if why != "" {
		e.failed[agent] = failure{why, e.now()}
	} else {
		delete(e.failed, agent)
	}
}

// NoteHold remembers why work could not reach the agent this tick; "" forgets it. contestable is for a reason a screen read gives and the
// agent's own state may contradict.
func (e *Env) NoteHold(agent, why string, contestable bool) {
	e.init()
	e.mu.Lock()
	defer e.mu.Unlock()
	if why != "" {
		e.noted[agent] = noted{why, e.now(), contestable}
	} else {
		delete(e.noted, agent)
	}
}

// HeldFreshS is how long a recorded reason stays true without being renewed.
const HeldFreshS = 30

// FailedReason is why the agent's latest wake or start failed, "" when that is not recent.
func (e *Env) FailedReason(agent string, now int64) string {
	e.init()
	e.mu.Lock()
	defer e.mu.Unlock()
	if f, ok := e.failed[agent]; ok && now-f.at <= HeldFreshS {
		return f.why
	}
	return ""
}

// HeldReason is (why, contestable) the agent's work was not typed on its latest try; ok false when that is not recent.
func (e *Env) HeldReason(agent string, now int64) (why string, contestable, ok bool) {
	e.init()
	e.mu.Lock()
	defer e.mu.Unlock()
	if n, have := e.noted[agent]; have && now-n.at <= HeldFreshS {
		return n.why, n.contestable, true
	}
	return "", false, false
}

func commas(n int64) string {
	s := fmt.Sprintf("%d", n)
	var out []byte
	for i, c := range []byte(s) {
		if i > 0 && (len(s)-i)%3 == 0 && c != '-' {
			out = append(out, ',')
		}
		out = append(out, c)
	}
	return string(out)
}

func cut(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}
