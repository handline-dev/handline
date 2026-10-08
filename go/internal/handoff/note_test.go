package handoff

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func (w *world) writeNote(agent, text string) {
	w.t.Helper()
	os.MkdirAll(filepath.Dir(w.NotePath(agent)), 0o755)
	if err := os.WriteFile(w.NotePath(agent), []byte(text), 0o644); err != nil {
		w.t.Fatal(err)
	}
}

func (w *world) asked(agent string) (int64, int, bool) {
	w.t.Helper()
	when, n, ok, err := w.Asked(agent)
	if err != nil {
		w.t.Fatal(err)
	}
	return when, n, ok
}

func (w *world) hold(agent string) bool {
	w.t.Helper()
	ok, err := w.Hold(agent, w.now)
	if err != nil {
		w.t.Fatal(err)
	}
	return ok
}

// longCache writes a transcript whose last response, at the given age, wrote an hour cache.
func (w *world) longCache(agent, session string, ageS int64) string {
	w.t.Helper()
	p := w.transcript("alpha", "-p", session)
	stamp := fmt.Sprintf(`"%s"`, timeFormat(w.now-ageS))
	line := `{"type":"assistant","timestamp":` + stamp + `,"message":{"usage":{"cache_creation":{"ephemeral_1h_input_tokens":9}}}}` + "\n"
	if err := os.WriteFile(p, []byte(line), 0o644); err != nil {
		w.t.Fatal(err)
	}
	w.status[agent] = [2]string{session, p}
	return p
}

func TestTheSwitchIsTheAgentsOwnElseTheGlobalOne(t *testing.T) {
	w := newWorld(t)
	if !w.Enabled("a") || !w.DefaultEnabled() {
		t.Fatal("on by default")
	}
	w.SetDefaultEnabled(false)
	if w.Enabled("a") {
		t.Error("the global switch is off")
	}
	w.settings.agent["a/handoff_note"] = true
	if !w.Enabled("a") || w.Enabled("b") {
		t.Error("the agent's own setting wins, and only for itself")
	}
}

func TestAnAskStandsUntilSomeoneElseTypesOrANewSessionBegins(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	w.telling.accept = true
	if _, _, ok := w.asked("a"); ok {
		t.Fatal("asked before any ask")
	}
	w.event("a", "Stop", "")
	ok, err := w.Ask("a", "close")
	if !ok || err != nil {
		t.Fatalf("%v %v", ok, err)
	}
	if len(w.telling.typed) != 1 || !strings.Contains(w.telling.typed[0], "Write your handoff to "+w.NotePath("a")+" now,") ||
		!strings.Contains(w.telling.typed[0], "At most 3000 characters") {
		t.Errorf("typed %q", w.telling.typed)
	}
	if when, n, ok := w.asked("a"); !ok || n != 0 || when != w.now {
		t.Errorf("asked: %v %v %v", when, n, ok)
	}
	w.event("a", "UserPromptSubmit", "") // the ask's own turn
	if _, n, ok := w.asked("a"); !ok || n != 1 {
		t.Errorf("after its turn: %v %v", n, ok)
	}
	if done, _ := w.AnsweredTurn("a"); !done {
		t.Error("a close-time ask answered by its turn is answered")
	}
	w.event("a", "UserPromptSubmit", "") // someone else typing
	if _, _, ok := w.asked("a"); ok {
		t.Error("a second prompt did not end the stretch")
	}
}

func TestANewSessionEndsTheStretch(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	w.telling.accept = true
	w.event("a", "Stop", "")
	w.Ask("a", "close")
	w.event("a", "SessionStart", "")
	if _, _, ok := w.asked("a"); ok {
		t.Error("the stretch outlived its session")
	}
}

func TestACacheTimeAskIsNotAReasonToClose(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	w.telling.accept = true
	w.event("a", "Stop", "")
	w.Ask("a", "cache")
	w.event("a", "UserPromptSubmit", "")
	if done, _ := w.AnsweredTurn("a"); done {
		t.Error("the cache ask's answer closed the session")
	}
	if !strings.Contains(strings.Join(w.logs, "\n"), "asked to write its handoff note") {
		t.Errorf("logs %q", w.logs)
	}
}

func TestAnAskNothingWasTypedForIsNoAsk(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	w.event("a", "Stop", "")
	w.writeNote("a", "stale")
	ok, err := w.Ask("a", "close") // the line cannot be typed now
	if ok || err != nil {
		t.Fatalf("%v %v", ok, err)
	}
	if _, _, asked := w.asked("a"); asked {
		t.Error("an ask that typed nothing stands")
	}
	if _, err := os.Stat(w.NotePath("a")); err == nil {
		t.Error("the file stayed before the ask")
	}
}

func TestTheNoteCountsOnlyInAnAskedStretchAndIsCleanedAndCut(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	w.writeNote("a", "early")
	if got, _ := w.NoteText("a"); got != "" {
		t.Errorf("a note with no ask: %q", got)
	}
	w.telling.accept = true
	w.event("a", "Stop", "")
	w.Ask("a", "close")
	if got, _ := w.NoteText("a"); got != "" {
		t.Errorf("no note yet: %q", got)
	}
	w.writeNote("a", "  \x1b[31mred\x1b[0m line\n\n")
	if got, _ := w.NoteText("a"); got != "red line" && got != "[31mred[0m line" {
		t.Errorf("cleaned: %q", got)
	}
	w.writeNote("a", "   \n")
	if got, _ := w.NoteText("a"); got != "" {
		t.Errorf("blank: %q", got)
	}
	w.writeNote("a", strings.Repeat("x", NoteMax)+"\n")
	if got, _ := w.NoteText("a"); got != strings.Repeat("x", NoteMax) {
		t.Errorf("exactly the most is cut: %d", len(got))
	}
	w.writeNote("a", strings.Repeat("x", NoteMax+10))
	if got, _ := w.NoteText("a"); got != strings.Repeat("x", NoteMax)+"\n(cut at 3000 characters)" {
		t.Errorf("a long note: %d", len(got))
	}
}

func TestTheCloseWaitsForTheAsksTurnButNeverPastTheLimit(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	w.telling.accept = true
	w.longCache("a", "s1", 600)
	w.event("a", "Stop", "")

	if !w.hold("a") || len(w.telling.typed) != 1 {
		t.Fatalf("the first look asks and holds: %q", w.telling.typed)
	}
	w.now += AskWaitS - 1
	if !w.hold("a") || len(w.telling.typed) != 1 {
		t.Error("held while the ask is young, and asked once")
	}
	w.now++
	if w.hold("a") {
		t.Error("held past AskWaitS")
	}
	w.event("a", "UserPromptSubmit", "")
	if w.hold("a") {
		t.Error("held after the ask's turn began")
	}
}

func TestTheCloseDoesNotAskWhenNothingWouldBeCheap(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	w.telling.accept = true

	if w.hold("a") {
		t.Error("asked a session with no transcript and no activity")
	}
	w.longCache("a", "s1", int64(LongTTLS-Margin(LongTTLS)))
	if w.hold("a") {
		t.Error("asked with the cache about to lapse")
	}
	w.longCache("a", "s1", 100)
	w.telling.busy["a"] = true
	if w.hold("a") {
		t.Error("asked an agent with background work")
	}
	w.telling.busy["a"] = false
	w.settings.agent["a/handoff_note"] = false
	if w.hold("a") {
		t.Error("asked an agent that turned the note off")
	}
	w.settings.agent["a/handoff_note"] = true
	if !w.hold("a") {
		t.Error("did not ask a warm session")
	}

	// A five-minute cache gets no ask.
	w2 := newWorld(t)
	w2.agent("a", "IDLE", "alpha", "")
	w2.telling.accept = true
	p := w2.transcript("alpha", "-p", "s2")
	os.WriteFile(p, []byte(`{"type":"assistant","timestamp":"`+timeFormat(w2.now-10)+`","message":{"usage":{"cache_creation":{"ephemeral_5m_input_tokens":9}}}}`+"\n"), 0o644)
	w2.status["a"] = [2]string{"s2", p}
	if w2.hold("a") {
		t.Error("asked a session on a five-minute cache")
	}

	// No clock to go by: an hour from its last activity, less the warm margin.
	w3 := newWorld(t)
	w3.agent("a", "IDLE", "alpha", "")
	w3.telling.accept = true
	w3.event("a", "Stop", "")
	if !w3.hold("a") {
		t.Error("did not ask a session active a moment ago with no transcript")
	}
	w4 := newWorld(t)
	w4.agent("a", "IDLE", "alpha", "")
	w4.telling.accept = true
	w4.event("a", "Stop", "")
	w4.now += ResumeWarmS
	if w4.hold("a") {
		t.Error("asked a session whose warm time has gone")
	}
}

func TestAnIdleSessionIsAskedOnceInTheLastTenthOfItsCacheLife(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	w.telling.accept = true
	w.event("a", "Stop", "") // the ask's cursor is the newest event: with none there is nothing to stand on
	edge := func(ageS int64) bool {
		w.t.Helper()
		p := w.longCache("a", "s1", ageS)
		ok, err := w.AtCacheEdge("a", w.now, p)
		if err != nil {
			w.t.Fatal(err)
		}
		return ok
	}
	if edge(int64(AskAt*LongTTLS) - 1) {
		t.Error("asked before 90% of the life")
	}
	if edge(int64(LongTTLS - AskMarginS)) {
		t.Error("asked with too little of the life left to reach a request")
	}
	if !edge(int64(AskAt * LongTTLS)) {
		t.Fatal("not asked at 90%")
	}
	if len(w.telling.typed) != 1 || !strings.Contains(strings.Join(w.logs, "\n"), "asked to write its handoff note") {
		t.Errorf("typed %q logs %q", w.telling.typed, w.logs)
	}
	if edge(int64(AskAt*LongTTLS) + 10) {
		t.Error("asked twice in one stretch")
	}
	if got, _ := w.AnsweredTurn("a"); got {
		t.Error("answered before any turn")
	}
}

func TestACacheEdgeAskNeedsAnOpenIdleSessionNotAtTheWall(t *testing.T) {
	cases := []struct {
		name  string
		setup func(w *world)
	}{
		{"running", func(w *world) { w.exec("UPDATE agent_state SET state = 'RUNNING'") }},
		{"closed", func(w *world) { w.exec("UPDATE agent_state SET closed_at = 5") }},
		{"at the wall", func(w *world) { w.event("a", "StopFailure", `{"limit": "rate_limit"}`) }},
		{"turned off", func(w *world) { w.settings.agent["a/handoff_note"] = false }},
		{"background work", func(w *world) { w.telling.busy["a"] = true }},
		{"an agent with no row", func(w *world) { w.exec("DELETE FROM agent_state") }},
	}
	for _, c := range cases {
		w := newWorld(t)
		w.agent("a", "IDLE", "alpha", "")
		w.telling.accept = true
		c.setup(w)
		p := w.longCache("a", "s1", int64(AskAt*LongTTLS))
		if ok, err := w.AtCacheEdge("a", w.now, p); ok || err != nil || len(w.telling.typed) != 0 {
			t.Errorf("%s: asked (%v %v)", c.name, ok, err)
		}
	}
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	w.telling.accept = true
	w.event("a", "StopFailure", `{"limit": "rate_limit"}`)
	w.event("a", "UserPromptSubmit", "") // working again
	p := w.longCache("a", "s1", int64(AskAt*LongTTLS))
	if ok, _ := w.AtCacheEdge("a", w.now, p); !ok {
		t.Error("an agent working again after the wall is not asked")
	}
	if ok, _ := w.AtCacheEdge("a", w.now, ""); ok {
		t.Error("asked with no transcript")
	}
}

func TestCacheLeftIsTheTranscriptsElseAnHourFromActivity(t *testing.T) {
	w := newWorld(t)
	w.agent("a", "IDLE", "alpha", "")
	if _, ok, _ := w.CacheLeft("a", float64(w.now)); ok {
		t.Error("something to go by with nothing to go by")
	}
	w.event("a", "Stop", "")
	if left, ok, _ := w.CacheLeft("a", float64(w.now)+100); !ok || left != PromptCacheS-100 {
		t.Errorf("from activity: %v %v", left, ok)
	}
	w.longCache("a", "s1", 1000)
	if left, ok, _ := w.CacheLeft("a", float64(w.now)); !ok || left != LongTTLS-1000 {
		t.Errorf("from the transcript: %v %v", left, ok)
	}
}

func TestAtLimitIsTheWallTheLastTurnEndedOn(t *testing.T) {
	w := newWorld(t)
	if got, err := AtLimit(w.DB, "a"); got != nil || err != nil {
		t.Fatalf("no events: %v %v", got, err)
	}
	w.event("a", "StopFailure", `{"limit": "rate_limit", "limit_source": "payload.error"}`)
	got, _ := AtLimit(w.DB, "a")
	if got == nil || got["limit"] != "rate_limit" || got["at"] != w.now {
		t.Fatalf("%v", got)
	}
	w.event("a", "UserPromptSubmit", "")
	if got, _ := AtLimit(w.DB, "a"); got != nil {
		t.Errorf("a prompt after the failure: %v", got)
	}
	w.event("a", "StopFailure", `{"error": "other"}`)
	if got, _ := AtLimit(w.DB, "a"); got != nil {
		t.Errorf("a failure that is not a limit: %v", got)
	}
	w.event("a", "StopFailure", "not json")
	if got, _ := AtLimit(w.DB, "a"); got != nil {
		t.Errorf("a payload that is not JSON: %v", got)
	}
	w.event("a", "StopFailure", "")
	if got, _ := AtLimit(w.DB, "a"); got != nil {
		t.Errorf("no payload: %v", got)
	}
}

func TestACommitIsRecordedOnceWithItsSubjectAndDiffstatCut(t *testing.T) {
	w := newWorld(t)
	for i := 0; i < 2; i++ {
		if err := RecordCommit(w.DB, "a", "/r", "abc123", strings.Repeat("s", 250), strings.Repeat("d", 350)); err != nil {
			t.Fatal(err)
		}
	}
	var n int
	var payload string
	w.DB.QueryRow("SELECT COUNT(*), MIN(payload) FROM events WHERE kind = 'commit'").Scan(&n, &payload)
	want := `{"repo": "/r", "sha": "abc123", "subject": "` + strings.Repeat("s", 200) + `", "diffstat": "` + strings.Repeat("d", 300) + `"}`
	if n != 1 || payload != want {
		t.Errorf("%d events, payload %q", n, payload)
	}
}
