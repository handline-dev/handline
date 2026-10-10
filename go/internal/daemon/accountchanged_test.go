package daemon

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/stream"
	"github.com/handline/handline/go/internal/weblogin"
)

// loginStates are the login states the accounts topic said for the account, in order.
func loginStates(t *testing.T, h *stream.Hub) (states []string) {
	t.Helper()
	rows, err := h.Log.Since(0, []string{"accounts"}, "", 100)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range rows {
		var a struct {
			Name  string
			Login struct{ State string }
		}
		if e.Type != "account.changed" || json.Unmarshal(e.Data, &a) != nil || a.Name != "main" {
			t.Fatalf("%s %s", e.Type, e.Data)
		}
		if strings.Contains(string(e.Data), "claude.example") || strings.Contains(string(e.Data), `"url"`) {
			t.Fatalf("the authorize URL is in the stream_log: %s", e.Data)
		}
		states = append(states, a.Login.State)
	}
	return
}

func loginDaemon(t *testing.T, script string) *Daemon {
	t.Helper()
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	home := t.TempDir()
	if err := os.MkdirAll(filepath.Join(home, "accounts", "main"), 0o755); err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(t.TempDir(), "claude")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\n"+script), 0o755); err != nil {
		t.Fatal(err)
	}
	db, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	d := &Daemon{db: db, cfg: Config{Home: home, Log: func(string, ...any) {}}, hub: stream.NewHub(),
		logins: &weblogin.Manager{Home: home, Lookup: func(string) (string, error) { return bin, nil }}}
	d.hub.Log = &stream.Log{DB: db}
	d.logins.Changed = d.accountChanged
	t.Cleanup(d.logins.Close)
	return d
}

func awaitStates(t *testing.T, d *Daemon, n int) []string {
	t.Helper()
	for i := 0; i < 400; i++ {
		if states := loginStates(t, d.hub); len(states) >= n {
			return states
		}
		time.Sleep(10 * time.Millisecond)
	}
	states := loginStates(t, d.hub)
	t.Fatalf("the accounts topic said %v, wanted %d states", states, n)
	return nil
}

func TestALoginSaysWaitingThenDoneOnTheAccountsTopic(t *testing.T) {
	// The login holds until the test has seen waiting: a login that finishes first would make the waiting event read the state it has
	// by then, and the topic would say done twice.
	release := filepath.Join(t.TempDir(), "release")
	d := loginDaemon(t, "echo https://claude.example/authorize?x=1\nwhile [ ! -e '"+release+"' ]; do sleep 0.02; done\ntouch \"$CLAUDE_CONFIG_DIR/.credentials.json\"\n")
	t.Setenv("SANDBOX_RUNTIME", "")
	t.Cleanup(func() { os.WriteFile(release, nil, 0o644) }) // a failure before the release must not leave the fake login spinning
	if _, err := d.logins.Start("main"); err != nil {
		t.Fatal(err)
	}
	if got := awaitStates(t, d, 1); got[0] != "waiting" {
		t.Fatalf("%v", got)
	}
	if err := os.WriteFile(release, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	got := awaitStates(t, d, 2)
	if got[0] != "waiting" || got[1] != "done" {
		t.Fatalf("%v", got)
	}
}

// What an account.changed says is the state of the move that announced it, not the state the login has when the announcement is made.
func TestAnAnnouncementSaysTheStateOfItsMoveNotTheLoginsStateNow(t *testing.T) {
	release := filepath.Join(t.TempDir(), "release")
	d := loginDaemon(t, "echo https://claude.example/authorize?x=1\nwhile [ ! -e '"+release+"' ]; do sleep 0.02; done\ntouch \"$CLAUDE_CONFIG_DIR/.credentials.json\"\n")
	t.Setenv("SANDBOX_RUNTIME", "")
	t.Cleanup(func() { os.WriteFile(release, nil, 0o644) })
	l, err := d.logins.Start("main")
	if err != nil {
		t.Fatal(err)
	}
	awaitStates(t, d, 1)
	if err := d.logins.Cancel(l.ID); err != nil {
		t.Fatal(err)
	}
	awaitStates(t, d, 2) // the login is cancelled now; an announcement of the earlier move must still say what that move was
	d.accountChanged("main", weblogin.Login{ID: l.ID, State: weblogin.CodeSent, Seq: 1 << 20})
	got := loginStates(t, d.hub)
	if got[len(got)-1] != "code_sent" {
		t.Fatalf("%v", got)
	}
}

func TestACancelledLoginSaysCancelledOnTheAccountsTopic(t *testing.T) {
	d := loginDaemon(t, "echo https://claude.example/authorize?x=1\ncat > /dev/null\n")
	l, err := d.logins.Start("main")
	if err != nil {
		t.Fatal(err)
	}
	if err := d.logins.Cancel(l.ID); err != nil {
		t.Fatal(err)
	}
	time.Sleep(300 * time.Millisecond) // the reaped child must not say a second end
	got := awaitStates(t, d, 2)
	if len(got) != 2 || got[0] != "waiting" || got[1] != "cancelled" {
		t.Fatalf("%v", got)
	}
}

// A login that prints its URL and exits at once moves twice in a breath; the two announcements are made on their own goroutines and must
// reach the topic in the order of the moves, so no login ends on a state it has left.
func TestTheTopicGetsTheMovesOfOneLoginInOrder(t *testing.T) {
	d := loginDaemon(t, "echo https://claude.example/authorize?x=1\nexit 1\n")
	wordsOf := func() map[string][]string { // login id -> the states the topic said for it, in order
		rows, err := d.hub.Log.Since(0, []string{"accounts"}, "", 1000)
		if err != nil {
			t.Fatal(err)
		}
		words := map[string][]string{}
		for _, e := range rows {
			var a struct {
				Login struct {
					ID    string `json:"login_id"`
					State string `json:"state"`
				}
			}
			if json.Unmarshal(e.Data, &a) != nil || a.Login.ID == "" {
				t.Fatalf("%s", e.Data)
			}
			words[a.Login.ID] = append(words[a.Login.ID], a.Login.State)
		}
		return words
	}
	ended := map[string]string{} // login id -> the state it ended in
	for i := 0; i < 60; i++ {
		d.logins.Start("main") // may refuse: the child can end before Start answers; the login is still the account's latest
		l := d.logins.Of("main")
		if l == nil {
			t.Fatal("no login recorded")
		}
		for {
			if now, _ := d.logins.ByID(l.ID); now.State != weblogin.Waiting && now.State != weblogin.CodeSent {
				ended[l.ID] = now.State
				break
			}
			runtime.Gosched()
		}
		for { // its end move has been announced
			if w := wordsOf()[l.ID]; len(w) > 0 && w[len(w)-1] == ended[l.ID] {
				break
			}
			runtime.Gosched()
		}
	}
	for id, words := range wordsOf() {
		for i, w := range words {
			if w != weblogin.Waiting && i != len(words)-1 {
				t.Fatalf("login %s: %v, a state after it ended", id, words)
			}
		}
		if words[len(words)-1] != ended[id] {
			t.Fatalf("login %s ended %s and the topic said %v", id, ended[id], words)
		}
	}
}

// An announcement made late, after one of a later move has gone out, is dropped: the topic never says an earlier state after a later one.
func TestAnAnnouncementOlderThanThePublishedOneIsDropped(t *testing.T) {
	d := loginDaemon(t, "exit 0\n")
	d.accountChanged("main", weblogin.Login{ID: "a", State: weblogin.Failed, Seq: 2})
	d.accountChanged("main", weblogin.Login{ID: "a", State: weblogin.Waiting, Seq: 1})
	if got := loginStates(t, d.hub); len(got) != 1 || got[0] != weblogin.Failed {
		t.Fatalf("%v", got)
	}
}
