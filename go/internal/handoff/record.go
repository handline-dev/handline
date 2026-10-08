package handoff

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

// What an agent is handed back when it comes back from a park or a move (handline/session/handoff.py). Four facts, read from what is
// on disk and in the database, with no model call:
//
//  1. the request it was working on (an agent has one delivered at a time),
//  2. the progress note the agent itself wrote on that request (`hl progress`),
//  3. the path of the session's transcript,
//  4. the worktree's branch, HEAD and git status.
//
// Nothing else. Everything a summary would add is in the transcript, and a summary is a model call, which fails when every account
// is walled, the moment a handoff is most needed. This one cannot fail on that: a fact that could not be read is named in the
// record, and the handoff is degraded only for that reason.
//
// The record is taken while the session is still live, because the agent's own status snapshot goes stale once it is closed and its
// transcript path with it. A close that failed to take one leaves none; the wake then takes it from the closed session's id,
// finding the transcript by name. It is stored on the agent's row. A wake that types it to the agent clears it, and so does a
// launch, which injects it into the system prompt: a record on the row is one no session has been handed.

// Heads is the delivery layer's answer to "which request was this agent told last" (*supervisor.Env).
type Heads interface {
	EffectiveHead(q db.Q, agent string) (*db.Request, error)
}

// Env is what the record is taken from.
type Env struct {
	DB   db.Q
	Home string
	Heads
	// AgentSession is the session id and transcript path the agent's own status snapshot names for an account (usage/status
	// read_for_agent); "" for what it does not know.
	AgentSession func(agent, account string) (session, transcript string)
	// MemoryLine is one line for the agent's next session when its memory needs tending, "" when it does not (team/memory).
	MemoryLine func(agent string) string
	Now        func() time.Time
	// Note is what asking for and reading the agent's own note needs.
	Note NoteEnv
}

func (e *Env) now() time.Time {
	if e.Now != nil {
		return e.Now()
	}
	return time.Now()
}

// statusLines is enough to see what is in flight; the rest is `git status` away.
const statusLines = 20

// ProgressMax: the note is the agent's own words about what is done and what is left; the handoff is typed into a fresh session, so
// it has to stay short.
const ProgressMax = 1500

func gitOut(worktree string, args ...string) (string, bool) {
	r := gitrun.Run(worktree, args...)
	return strings.TrimRight(r.Stdout, "\n"), r.RC == 0
}

func cutRunes(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

func delivered(r *db.Request) bool {
	return r != nil && r.DeliveredAt.Valid && r.DeliveredAt.Int64 != 0
}

// RequestLine is the delivered request: the agent's effective head, not the true head, which a blocked head would name even though
// the agent was never told it and is working on whatever stood in for it instead. None delivered is a fact about the agent, not a
// failure to read one: an agent between requests has nothing in progress.
func (e *Env) RequestLine(agent string) (string, error) {
	head, err := e.EffectiveHead(e.DB, agent)
	if err != nil {
		return "", err
	}
	if !delivered(head) {
		return "request: none in progress", nil
	}
	first := ""
	if lines := textsafe.SplitLines(head.Body); len(lines) > 0 {
		first = cutRunes(lines[0], 120)
	}
	return fmt.Sprintf("request: #%d from %s - %s", head.ID, head.Requester, first), nil
}

func progressKey(requestID int64) string { return fmt.Sprintf("progress:%d", requestID) }
func taskProgressKey(tid int64) string   { return fmt.Sprintf("progress-task:%d", tid) }

// SetProgress keeps note as the progress on the agent's delivered request (its effective head: the one it was actually told, not a
// blocked true head it never saw), replacing the last one. With no request in progress it is kept on the task the agent holds, or
// on the agent when it holds none, so a deferred agent can still leave a handoff note. An empty note clears it. ok is false, with
// the reason in msg, for a note too long.
func (e *Env) SetProgress(agent, note string) (ok bool, msg string, err error) {
	head, err := e.EffectiveHead(e.DB, agent)
	if err != nil {
		return false, "", err
	}
	note = textsafe.Strip(textsafe.Text(note))
	if n := len([]rune(note)); n > ProgressMax {
		return false, fmt.Sprintf("%d characters; the handoff carries at most %d - keep what is done and what is left", n, ProgressMax), nil
	}
	if !delivered(head) {
		tid, has, err := db.CurrentTask(e.DB, agent)
		if err != nil {
			return false, "", err
		}
		if has {
			return true, fmt.Sprintf("T%d (no request in progress)", tid), db.SetMeta(e.DB, taskProgressKey(tid), note)
		}
		return true, "you (no request in progress)", db.SetMeta(e.DB, db.AgentProgressKey(agent), note)
	}
	return true, fmt.Sprintf("#%d", head.ID), db.SetMeta(e.DB, progressKey(head.ID), note)
}

// ProgressOf is the progress note kept on this request, whether or not it is the head; "" for none.
func (e *Env) ProgressOf(requestID int64) (string, error) {
	v, _, err := db.Meta(e.DB, progressKey(requestID))
	return v, err
}

// GetProgress is the note kept on the agent's effective head, or on the current task, else on the agent, when it has no request in
// progress; "" for none.
func (e *Env) GetProgress(agent string) (string, error) {
	head, err := e.EffectiveHead(e.DB, agent)
	if err != nil {
		return "", err
	}
	var key string
	if delivered(head) {
		key = progressKey(head.ID)
	} else {
		tid, has, err := db.CurrentTask(e.DB, agent)
		if err != nil {
			return "", err
		}
		key = db.AgentProgressKey(agent)
		if has {
			key = taskProgressKey(tid)
		}
	}
	v, _, err := db.Meta(e.DB, key)
	return v, err
}

func (e *Env) progressLines(agent string) ([]string, error) {
	note, err := e.GetProgress(agent)
	if err != nil || note == "" {
		return nil, err
	}
	lines := []string{"progress (written by the agent):"}
	for _, l := range textsafe.SplitLines(note) {
		lines = append(lines, "  "+l)
	}
	return lines, nil
}

var sessionIDRE = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]*$`)

// FindTranscript is where a session's transcript is, found by its id rather than by a status snapshot: nothing refreshes a closed
// agent's snapshot, so by the time a closed session is looked at the snapshot no longer names it. The account it ran under is
// searched first. "" when there is none.
func (e *Env) FindTranscript(session, account string) string {
	if session == "" || !sessionIDRE.MatchString(session) {
		return ""
	}
	var accts []string
	if account != "" {
		accts = append(accts, account)
	}
	for _, a := range paths.ListAccounts(e.Home) {
		if a != account {
			accts = append(accts, a)
		}
	}
	// One directory level under the vendor's memory root is where a transcript is written, so the search is that deep and no deeper.
	for _, acct := range accts {
		v := team.AccountVendor(e.Home, acct)
		if team.CheckVendor(v) != nil || v != team.DefaultVendor {
			continue // an unknown vendor, or one (opencode) whose sessions are rows in its database
		}
		dir, err := team.AccountConfigDir(e.Home, acct)
		if err != nil {
			continue
		}
		found, _ := filepath.Glob(filepath.Join(dir, "projects", "*", session+".jsonl"))
		if len(found) > 0 {
			sort.Strings(found)
			return found[0]
		}
	}
	return ""
}

// talkedEvents are the events a session that has actually been talked to leaves behind. A fresh or just-cleared session has only
// its SessionStart, so it is never mistaken for the one a restart came from.
var talkedEvents = []string{"UserPromptSubmit", "Stop", "StopFailure"}

// lastTalkedSession is (session id, transcript path) from the agent's newest hook event of a session that was actually used, for
// when the status snapshot names neither.
func (e *Env) lastTalkedSession(agent string) (session, path string, err error) {
	rows, err := e.DB.Query("SELECT payload FROM events WHERE agent = ? AND kind IN (?, ?, ?) ORDER BY id DESC LIMIT 20",
		agent, talkedEvents[0], talkedEvents[1], talkedEvents[2])
	if err != nil {
		return "", "", err
	}
	defer rows.Close()
	for rows.Next() {
		var payload *string
		if err := rows.Scan(&payload); err != nil {
			return "", "", err
		}
		if payload == nil {
			continue
		}
		var data map[string]any
		if json.Unmarshal([]byte(*payload), &data) != nil {
			continue
		}
		if s, ok := data["session_id"].(string); ok {
			p, _ := data["transcript_path"].(string)
			return s, p, nil
		}
	}
	return "", "", rows.Err()
}

// PreviousSession is (session id, transcript path) of the session an agent is restarting from, as much of it as can be found: the
// status snapshot, then the agent's own hook events for an id, then the id looked up by name for a path.
func (e *Env) PreviousSession(agent, account, closedSession string) (session, path string, err error) {
	if closedSession != "" {
		session = closedSession
	} else {
		if e.AgentSession != nil {
			session, path = e.AgentSession(agent, account)
		}
		if session == "" || path == "" {
			evSession, evPath, err := e.lastTalkedSession(agent)
			if err != nil {
				return "", "", err
			}
			if session == "" {
				session = evSession
			}
			if path == "" && evSession == session {
				path = evPath
			}
		}
	}
	if session != "" && path == "" {
		path = e.FindTranscript(session, account)
	}
	return session, path, nil
}

func (e *Env) sessionLines(agent, account, closedSession string) (lines []string, gap string, err error) {
	session, path, err := e.PreviousSession(agent, account, closedSession)
	if err != nil {
		return nil, "", err
	}
	if path == "" {
		return nil, "the session's transcript path", nil
	}
	return []string{fmt.Sprintf("previous session: %s, recorded at %s", session, path),
		"Grep it for what you need - do not read it whole, it is a JSONL transcript with every tool result in it."}, "", nil
}

func worktreeLines(worktree string) (lines []string, gap string) {
	if st, err := os.Stat(worktree); worktree == "" || err != nil || !st.IsDir() {
		return nil, "the worktree (none recorded, or gone)"
	}
	branch, ok1 := gitOut(worktree, "rev-parse", "--abbrev-ref", "HEAD")
	head, ok2 := gitOut(worktree, "rev-parse", "--short", "HEAD")
	dirty, ok3 := gitOut(worktree, "status", "--porcelain")
	if !ok1 || !ok2 || !ok3 {
		return nil, "the worktree's branch, HEAD or git status"
	}
	lines = []string{"worktree: " + worktree, fmt.Sprintf("branch: %s @ %s", textsafe.Oneline(branch), textsafe.Oneline(head))}
	// A status line names a path the agent's own worktree chose, the same class of text a filename is, and not one git quotes for
	// us here (`status --porcelain` is meant for scripts to parse, not shown to a terminal).
	changed := textsafe.SplitLines(dirty)
	if len(changed) == 0 {
		lines = append(lines, "git status: clean")
	} else {
		lines = append(lines, "git status:")
	}
	for _, c := range changed[:min(len(changed), statusLines)] {
		lines = append(lines, "  "+textsafe.Oneline(c))
	}
	if len(changed) > statusLines {
		lines = append(lines, fmt.Sprintf("  ... and %d more", len(changed)-statusLines))
	}
	return lines, ""
}

// WorktreePath is the directory an agent works in, out of what its row holds. `hl launch` records what Orca handed it, which is a
// selector (the repo's id, then `::`, then the path) while `spawn` records a plain path; "" when there is none or it is gone.
func WorktreePath(recorded string) string {
	if recorded == "" {
		return ""
	}
	path := recorded
	if i := strings.Index(recorded, "::"); i >= 0 {
		path = recorded[i+2:]
	}
	if st, err := os.Stat(path); err == nil && st.IsDir() {
		return path
	}
	return ""
}

func (e *Env) noteLines(agent string) ([]string, error) {
	body, err := e.NoteText(agent)
	if err != nil || body == "" {
		return nil, err
	}
	lines := []string{"written by the agent just before the close:"}
	for _, l := range textsafe.SplitLines(body) {
		lines = append(lines, "  "+l)
	}
	return lines, nil
}

// Capture is the record, and what could not be read for it (empty when it is whole). closedSession is the id of a session that has
// already ended: its transcript is then looked up by id instead of read from the live snapshot.
func (e *Env) Capture(agent, closedSession string) (string, []string, error) {
	row, err := db.GetAgentState(e.DB, agent)
	if err != nil {
		return "", nil, err
	}
	worktree := ""
	if row != nil {
		worktree = WorktreePath(row.Worktree.String)
	}
	request, err := e.RequestLine(agent)
	if err != nil {
		return "", nil, err
	}
	lines := []string{"# handoff · " + agent, "captured: " + e.now().Format("2006-01-02 15:04:05"), request}
	progress, err := e.progressLines(agent)
	if err != nil {
		return "", nil, err
	}
	lines = append(lines, progress...)
	if e.MemoryLine != nil {
		if note := e.MemoryLine(agent); note != "" {
			lines = append(lines, note)
		}
	}
	account, err := team.CurrentAccount(e.DB, agent)
	if err != nil {
		return "", nil, err
	}
	var missing []string
	sess, gap, err := e.sessionLines(agent, account, closedSession)
	if err != nil {
		return "", nil, err
	}
	wt, wtGap := worktreeLines(worktree)
	for _, part := range []struct {
		got []string
		gap string
	}{{sess, gap}, {wt, wtGap}} {
		lines = append(lines, part.got...)
		if part.gap != "" {
			missing = append(missing, part.gap)
			lines = append(lines, fmt.Sprintf("(could not read %s)", part.gap))
		}
	}
	note, err := e.noteLines(agent)
	if err != nil {
		return "", nil, err
	}
	return strings.Join(append(lines, note...), "\n"), missing, nil
}

// Record captures the record and keeps it for the agent's next start. It returns what was missing.
func (e *Env) Record(agent, closedSession string) ([]string, error) {
	text, missing, err := e.Capture(agent, closedSession)
	if err != nil {
		return nil, err
	}
	if err := db.SetHandoff(e.DB, agent, text); err != nil {
		return nil, err
	}
	return missing, e.ClearNote(agent) // it is in the record now
}

func (e *Env) accountOf(agent string) (string, error) { return team.CurrentAccount(e.DB, agent) }

func isNoRows(err error) bool { return err == sql.ErrNoRows }
