package supervisor

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"regexp"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
)

// A request is delivered once the receiving session's transcript shows it, not once it was typed (daemon/landing.py).
// One rule for a queued line, repeated every tick: the transcript first - there, it is landed - and not there, typed unless this daemon
// typed it less than LandingWaitS ago (kept in memory only, so a restart at worst types it once more). A line that never lands stays
// queued, on the stuck list: there is no count and no giving up.

const (
	LandingWaitS = 90
	// tailBytes is the end of a transcript where a landing is, seconds after the send.
	tailBytes = 4 << 20
)

// NoteTyped records that this daemon typed request rid now.
func (e *Env) NoteTyped(rid int64) {
	e.init()
	e.mu.Lock()
	e.typedAt[rid] = e.now()
	e.mu.Unlock()
}

func (e *Env) typedRecently(rid, now int64) bool {
	e.mu.Lock()
	at, ok := e.typedAt[rid]
	e.mu.Unlock()
	if !ok {
		at = -LandingWaitS
	}
	return now-at < LandingWaitS
}

// LandingState is landing.state: where a request is on its way into its target's session: queued (not typed), typed or delivered.
func LandingState(r *db.Request) string {
	switch {
	case r.LandedAt.Valid && r.LandedAt.Int64 != 0:
		return "delivered"
	case !r.TypedAt.Valid || r.TypedAt.Int64 == 0:
		return "queued"
	}
	return "typed"
}

// entryTexts is the text a transcript entry holds that a person or the daemon typed. Never a tool result: `hl inbox` prints the very header looked for.
func entryTexts(entry map[string]any) []string {
	var out []string
	add := func(v any) {
		if s, ok := v.(string); ok {
			out = append(out, s)
		}
	}
	kind, _ := entry["type"].(string)
	switch {
	case kind == "queue-operation" && entry["operation"] == "enqueue":
		add(entry["content"])
	case kind == "attachment":
		if att, ok := entry["attachment"].(map[string]any); ok && att["type"] == "queued_command" {
			add(att["prompt"])
		}
	case kind == "user":
		if msg, ok := entry["message"].(map[string]any); ok {
			switch c := msg["content"].(type) {
			case string:
				add(c)
			case []any:
				for _, b := range c {
					if blk, ok := b.(map[string]any); ok && blk["type"] == "text" {
						add(blk["text"])
					}
				}
			}
		}
	}
	return out
}

func holds(line []byte, rid int64, header *regexp.Regexp) bool {
	if !bytes.Contains(line, []byte(fmt.Sprintf("#%d from ", rid))) {
		return false
	}
	var entry map[string]any
	if json.Unmarshal(line, &entry) != nil {
		return false
	}
	for _, t := range entryTexts(entry) {
		if header.MatchString(t) {
			return true
		}
	}
	return false
}

// ShownIn is whether the transcript holds the line that tells an agent about request rid: its last tailBytes, where a landing is; whole
// reads it all, for the one look that decides a line was never shown.
func ShownIn(transcript string, rid int64, whole bool) bool {
	header := regexp.MustCompile(fmt.Sprintf(`#%d from [^\n]*waiting for you:`, rid))
	f, err := os.Open(transcript)
	if err != nil {
		return false
	}
	defer f.Close()
	var r io.Reader = f
	if !whole {
		size, err := f.Seek(0, io.SeekEnd)
		if err != nil {
			return false
		}
		start := size - tailBytes
		if start < 0 {
			start = 0
		}
		if _, err := f.Seek(start, io.SeekStart); err != nil {
			return false
		}
	} else if _, err := f.Seek(0, io.SeekStart); err != nil {
		return false
	}
	sc := bufio.NewReaderSize(r, 1<<20)
	for {
		line, err := sc.ReadBytes('\n')
		if len(line) > 0 && holds(line, rid, header) {
			return true
		}
		if err != nil {
			return false
		}
	}
}

func (e *Env) transcriptsOf(sessions []string, acct string) []string {
	var out []string
	for _, s := range sessions {
		if p := e.Sessions.FindTranscript(s, acct); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func distinct(xs ...string) []string {
	var out []string
	seen := map[string]bool{}
	for _, x := range xs {
		if x != "" && !seen[x] {
			seen[x] = true
			out = append(out, x)
		}
	}
	return out
}

func (e *Env) oweAgain(q db.Q, agent string, rid int64) {
	check(db.OweAgain(q, rid))
	if must(db.Cursor(q, "notify:"+agent)) == rid {
		check(db.ForgetTold(q, agent)) // a task head is told again only when the cursor is void
	}
}

func (e *Env) accountOf(q db.Q, agent string) string { return must(team.CurrentAccount(q, agent)) }

// LandedAlready: the transcript first. Is rid already in the agent's session? Then it is landed and typing it again would only repeat
// it. False when nothing says where to look.
func (e *Env) LandedAlready(q db.Q, agent string, rid int64) bool {
	acct := e.accountOf(q, agent)
	if acct == "" || !e.Sessions.KeepsTranscripts(acct) {
		return false
	}
	typedSession := ""
	if row := must(db.GetRequest(q, rid)); row != nil {
		typedSession = row.TypedSess.String
	}
	for _, p := range e.transcriptsOf(distinct(typedSession, e.liveSession(q, agent)), acct) {
		if ShownIn(p, rid, false) {
			return true
		}
	}
	return false
}

// LandingSweep confirms or owes again every request typed at the agent and not yet shown in its transcript: shown, it is landed; not shown
// and not typed within LandingWaitS by this daemon (or its session gone), it is typed again, without end.
func (e *Env) LandingSweep(q db.Q, agent string) (err error) {
	defer Catch(&err)
	rows := must(db.AwaitingLanding(q, agent))
	if len(rows) == 0 {
		return nil
	}
	now := e.now()
	acct := e.accountOf(q, agent)
	if acct == "" {
		return nil // nothing says where to look, so nothing is claimed either way
	}
	if !e.Sessions.KeepsTranscripts(acct) {
		for _, r := range rows {
			check(db.MarkLanded(q, r.ID))
		}
		return nil
	}
	live := e.liveSession(q, agent)
	state := stateOf(q, agent)
	for _, r := range rows {
		sessions := distinct(r.TypedSess.String, live)
		if len(sessions) == 0 {
			continue // no session id is known yet: a missing line cannot be told from a missing id
		}
		// Typed into a session that is gone is not worth waiting out: nothing will read it.
		gone := r.TypedSess.String != "" && ((state != nil && state.ClosedAt.Valid && state.ClosedAt.Int64 != 0) || (live != "" && live != r.TypedSess.String))
		expired := gone || !e.typedRecently(r.ID, now)
		shown := false
		for _, p := range e.transcriptsOf(sessions, acct) {
			if ShownIn(p, r.ID, expired) {
				shown = true
				break
			}
		}
		if shown {
			check(db.MarkLanded(q, r.ID))
			must(db.AddEvent(q, agent, "landed", db.KV{K: "request_id", V: r.ID}, db.KV{K: "after_s", V: now - r.TypedAt.Int64}))
			e.log("  %s: #%d is in its transcript", agent, r.ID)
			continue
		}
		if !expired {
			continue
		}
		e.oweAgain(q, agent, r.ID)
		must(db.AddEvent(q, agent, "retyped", db.KV{K: "request_id", V: r.ID}, db.KV{K: "gone", V: gone}))
		ended := ""
		if gone {
			ended = " (its session ended)"
		}
		e.log("  %s: #%d never reached its transcript%s - typed again", agent, r.ID, ended)
	}
	return nil
}
