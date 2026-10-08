package handoff

import (
	"fmt"
	"os"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/textsafe"
)

// The agent's own part of its handoff (handline/daemon/handoff_note.py): a file it is asked to write while its prompt cache is still
// warm (a turn then reads the context at the cached price), which the mechanical record carries into the next session if that one
// starts fresh.
//
// It is asked at two moments. Just before a close or a move, once per stretch. And, for a session that stays open and idle, at 90%
// of its cache's real life (an hour for a subscription, read off its last response): the cache is about to be paid for in full, so
// the turn that writes the note is the cheap moment to write it. A cache whose life is five minutes gets no ask at all; the record is
// then the facts alone. An agent turns it off for itself with `handoff_note: false` in its agent.yaml (config.yaml's `handoff_note`
// is the global switch).
//
// It writes the note file and stops. The ask is never waited on past AskWaitS, and an agent that wrote nothing, or could not (the
// wall, an error), is handed the mechanical record alone.
//
// The ask is remembered in one cursor: its row id is the newest event when the ask was typed, and its time is when. The turn that
// answers it is the one UserPromptSubmit after that event; a second one is someone else typing, and a SessionStart is a new
// session: either ends the stretch, and the note no longer counts. A record that carries the note ends it too. The file is removed
// before every ask, so one that exists was written after it.
//
// No repeats: the stretch is also what keeps a second note from being asked for. While no one has typed since the ask's own turn,
// the stretch stands, so an idle session is asked once however long it sits; a delivered message or a working turn ends it, and the
// next idle stretch asks again.
const (
	// AskAt is the share of its cache's life an open idle session is asked at, and AskMarginS how close to the end the ask is still
	// worth making (the turn has to reach its first request before the cache is gone).
	AskAt      = 0.9
	AskMarginS = 60
	// AskWaitS is how long a typed ask may go without its turn starting before the close goes on.
	AskWaitS = 300
	// NoteMax is what the record carries of the note; the ask names it so the agent keeps to it.
	NoteMax = 3000

	// NoteKey is the setting that turns it off.
	NoteKey = "handoff_note"

	// PromptCacheS is the life of the prompt cache, and ResumeWarmS how long after its last activity a resume still finds it warm
	// (daemon/states.py).
	PromptCacheS = 3600
	ResumeWarmS  = PromptCacheS - 300
)

const askText = "Write your handoff to %s now, while your prompt cache is warm, with one Write " +
	"(replace what is there), then stop and run nothing else. The next session of you, if " +
	"it starts fresh, reads it first: copy yourself into it. At most %d characters, about " +
	"30 lines, only what `hl` and your memory cannot tell it: (1) what you were deciding " +
	"and why; (2) what you meant to do next; (3) promises or doubts not yet on any task; " +
	"(4) the founder's preferences and the pitfalls you learned this session; (5) who you " +
	"wait on, as T<n>/#<n> numbers only. Do not restate what a task or message holds; cite " +
	"its T or # number. A lasting lesson or preference goes to your memory first, not here. " +
	"If there is nothing, write the single word: nothing."

// Telling is what the ask needs of the delivery layer (*supervisor.Env).
type Telling interface {
	HasBackgroundWork(q db.Q, agent string) (bool, error)
	// TypeNotice types one line at the agent's open session if it may be typed at now.
	TypeNotice(q db.Q, agent, text string) (bool, error)
}

// Settings is config.yaml's global flags and agent.yaml's own booleans.
type Settings interface {
	Flag(key string, def bool) bool
	SetFlag(key string, on bool) error
	// AgentFlag is the agent's own boolean under key; ok is false when it sets none.
	AgentFlag(agent, key string) (value, ok bool)
}

// NoteEnv is what the note asks and reads; it sits on the record's Env.
type NoteEnv struct {
	Settings
	Telling
	Log func(format string, a ...any)
}

func (e *Env) log(format string, a ...any) {
	if e.Note.Log != nil {
		e.Note.Log(format, a...)
	}
}

// NotePath is the one file an agent writes for its handoff note.
func (e *Env) NotePath(agent string) string { return paths.HandoffNoteFile(e.Home, agent) }

func noteKey(agent string) string     { return "handoff_ask:" + agent }
func noteKindKey(agent string) string { return "handoff_ask_kind:" + agent }

// DefaultEnabled is the global switch: what an agent with no `handoff_note` of its own follows. On unless config.yaml says off.
func (e *Env) DefaultEnabled() bool { return e.Note.Flag(NoteKey, true) }

// SetDefaultEnabled turns the global switch on or off.
func (e *Env) SetDefaultEnabled(on bool) error { return e.Note.SetFlag(NoteKey, on) }

// Enabled is whether this agent writes a handoff note at all: its own `handoff_note` in agent.yaml, else the global switch.
func (e *Env) Enabled(agent string) bool {
	if mine, ok := e.Note.AgentFlag(agent, NoteKey); ok {
		return mine
	}
	return e.DefaultEnabled()
}

func (e *Env) promptsSince(agent string, eventID int64) (n int, err error) {
	err = e.DB.QueryRow("SELECT COUNT(*) FROM events WHERE agent = ? AND kind = 'UserPromptSubmit' AND id > ?", agent, eventID).Scan(&n)
	return n, err
}

// Asked is (when, prompts typed since) for this stretch's ask; ok is false when there is none: never asked, someone else typed
// after the ask's own turn, or a new session began.
func (e *Env) Asked(agent string) (when int64, prompts int, ok bool, err error) {
	eventID, err := db.Cursor(e.DB, noteKey(agent))
	if err != nil {
		return 0, 0, false, err
	}
	when, has, err := db.CursorUpdated(e.DB, noteKey(agent))
	if err != nil || eventID == 0 || !has {
		return 0, 0, false, err
	}
	var one int
	switch err := e.DB.QueryRow("SELECT 1 FROM events WHERE agent = ? AND kind = 'SessionStart' AND id > ? LIMIT 1", agent, eventID).Scan(&one); {
	case err == nil:
		return 0, 0, false, nil
	case !isNoRows(err):
		return 0, 0, false, err
	}
	n, err := e.promptsSince(agent, eventID)
	if err != nil || n > 1 {
		return 0, 0, false, err
	}
	return when, n, true, nil
}

// AnsweredTurn is whether the turn answering a close-time ask has come and gone. Idle is the caller's to know. An ask made because
// the cache was about to expire is not a reason to close: the session stays open.
func (e *Env) AnsweredTurn(agent string) (bool, error) {
	_, prompts, ok, err := e.Asked(agent)
	if err != nil || !ok || prompts != 1 {
		return false, err
	}
	kind, _, err := db.Meta(e.DB, noteKindKey(agent))
	return kind != "cache", err
}

// NoteText is what the agent wrote for this stretch, cleaned and cut to NoteMax, "" for none.
func (e *Env) NoteText(agent string) (string, error) {
	_, _, ok, err := e.Asked(agent)
	if err != nil || !ok {
		return "", err
	}
	raw, err := os.ReadFile(e.NotePath(agent))
	if err != nil {
		return "", nil
	}
	body := textsafe.Strip(textsafe.Text(strings.ToValidUTF8(string(raw), "�")))
	if body == "" {
		return "", nil
	}
	if r := []rune(body); len(r) > NoteMax {
		return strings.TrimRightFunc(string(r[:NoteMax]), textsafe.IsSpace) + fmt.Sprintf("\n(cut at %d characters)", NoteMax), nil
	}
	return body, nil
}

// ClearNote: the note is in the record; drop it and the ask.
func (e *Env) ClearNote(agent string) error {
	if err := os.Remove(e.NotePath(agent)); err != nil && !os.IsNotExist(err) {
		return err
	}
	if err := db.ClearCursor(e.DB, noteKey(agent)); err != nil {
		return err
	}
	return db.SetMeta(e.DB, noteKindKey(agent), "")
}

// CacheClock is the prompt-cache clock of this agent's session (session, if it is a closed one's id), read from its transcript; nil
// when there is no transcript to read.
func (e *Env) CacheClock(agent, session string) (*Clock, error) {
	account, err := e.accountOf(agent)
	if err != nil {
		return nil, err
	}
	_, path, err := e.PreviousSession(agent, account, session)
	if err != nil {
		return nil, err
	}
	return ReadClock(path), nil
}

// CacheLeft is the seconds until this agent's cache lapses, negative once it has; ok is false with nothing to go by: the real TTL
// off its last response when the transcript says, else an hour from its last activity.
func (e *Env) CacheLeft(agent string, now float64) (left float64, ok bool, err error) {
	clock, err := e.CacheClock(agent, "")
	if err != nil {
		return 0, false, err
	}
	if clock != nil && clock.TTL != 0 {
		left, ok = clock.Left(now)
		return left, ok, nil
	}
	last, has, err := db.LastActivity(e.DB, agent)
	if err != nil || !has {
		return 0, false, err
	}
	return float64(last) + PromptCacheS - now, true, nil
}

// eligible is whether a close or move may ask now: the agent writes notes, nothing runs behind its prompt, and a turn would still
// be cheap: its cache has a life over five minutes and it is not nearly over.
func (e *Env) eligible(agent string, now int64) (bool, error) {
	if !e.Enabled(agent) {
		return false, nil
	}
	if busy, err := e.Note.HasBackgroundWork(e.DB, agent); err != nil || busy {
		return false, err
	}
	clock, err := e.CacheClock(agent, "")
	if err != nil {
		return false, err
	}
	if clock != nil && clock.TTL != 0 {
		return clock.TTL >= LongTTLS && float64(now)-clock.At < float64(clock.TTL-Margin(clock.TTL)), nil
	}
	last, has, err := db.LastActivity(e.DB, agent)
	return has && now-last < ResumeWarmS, err
}

// Ask removes the note file, remembers the ask, and types it. False when nothing was typed: no ask was made.
func (e *Env) Ask(agent, kind string) (bool, error) {
	path := e.NotePath(agent)
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return false, err
	}
	var lastEvent int64
	if err := e.DB.QueryRow("SELECT COALESCE(MAX(id), 0) FROM events").Scan(&lastEvent); err != nil {
		return false, err
	}
	if err := db.SetCursor(e.DB, noteKey(agent), lastEvent); err != nil {
		return false, err
	}
	if err := db.SetMeta(e.DB, noteKindKey(agent), kind); err != nil {
		return false, err
	}
	typed, err := e.Note.TypeNotice(e.DB, agent, fmt.Sprintf(askText, path, NoteMax))
	if err != nil {
		return false, err
	}
	if typed {
		note := ""
		if kind == "cache" {
			note = " before its cache expires"
		}
		e.log("  %s: asked to write its handoff note%s", agent, note)
		return true, nil
	}
	// nothing was typed: no ask was made
	if err := db.ClearCursor(e.DB, noteKey(agent)); err != nil {
		return false, err
	}
	return false, db.SetMeta(e.DB, noteKindKey(agent), "")
}

// AtCacheEdge: an open, idle session whose cache is nearly spent is asked for its note, once.
//
// Only a cache with a life of an hour or more: at five minutes there is no warm moment worth a turn. Only once per stretch (Asked),
// and only inside the last tenth of the life, which a session that was working never reaches, since each response restarts the
// clock. A session that has since closed, or whose line cannot be typed now (a turn running, a dialog open, the wall), is simply
// asked on a later tick while the window lasts; one the ask cannot reach is left to the facts. A closed session, one that is not
// idle, and one at the usage wall are not asked. transcriptPath is the one the agent's status snapshot names.
func (e *Env) AtCacheEdge(agent string, now int64, transcriptPath string) (bool, error) {
	row, err := db.GetAgentState(e.DB, agent)
	if err != nil || row == nil || row.ClosedAt.Valid && row.ClosedAt.Int64 != 0 || row.State != "IDLE" {
		return false, err
	}
	if limit, err := AtLimit(e.DB, agent); err != nil || limit != nil {
		return false, err // not an open, idle session that can take a turn
	}
	if !e.Enabled(agent) {
		return false, nil
	}
	if _, _, asked, err := e.Asked(agent); err != nil || asked {
		return false, err
	}
	if busy, err := e.Note.HasBackgroundWork(e.DB, agent); err != nil || busy {
		return false, err
	}
	clock := ReadClock(transcriptPath)
	if clock == nil || clock.TTL < LongTTLS {
		return false, nil
	}
	elapsed := float64(now) - clock.At
	if !(AskAt*float64(clock.TTL) <= elapsed && elapsed < float64(clock.TTL-AskMarginS)) {
		return false, nil
	}
	return e.Ask(agent, "cache")
}

// Hold is whether the close or move this agent is due should wait for its note. It asks once per stretch and holds while the ask's
// turn has not begun, for at most AskWaitS. False means go on, with the note if it was written and without it otherwise: never a
// block.
func (e *Env) Hold(agent string, now int64) (bool, error) {
	when, prompts, asked, err := e.Asked(agent)
	if err != nil {
		return false, err
	}
	if !asked {
		ok, err := e.eligible(agent, now)
		if err != nil || !ok {
			return false, err
		}
		return e.Ask(agent, "close")
	}
	return prompts == 0 && now-when < AskWaitS, nil
}
