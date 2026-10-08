package usagepoll

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/structured"
)

type fakeCLI struct {
	resp   map[string]any
	err    error
	sent   string
	result map[string]any
	h      structured.Handlers
	closed bool
}

func (f *fakeCLI) GetUsage(context.Context) (map[string]any, error) { return f.resp, f.err }
func (f *fakeCLI) Close() error                                     { f.closed = true; return nil }
func (f *fakeCLI) Send(_, text string, _ structured.Delivery) (structured.Receipt, error) {
	f.sent = text
	if f.result != nil {
		go f.h.OnEvent(f.result)
	}
	return structured.Receipt{}, nil
}

func meterWith(t *testing.T, f *fakeCLI, openErr error) (*Meter, structured.Launch) {
	t.Helper()
	home := t.TempDir()
	if err := os.MkdirAll(paths.AccountDir(home, "a"), 0o755); err != nil {
		t.Fatal(err)
	}
	var got structured.Launch
	return &Meter{Home: home, Claude: "claude", Now: func() time.Time { return time.Unix(now, 0) },
		Open: func(_ context.Context, l structured.Launch, h structured.Handlers) (CLI, error) {
			got = l
			f.h = h
			return f, openErr
		}}, got
}

func answer(five, seven any, reset string) map[string]any {
	return map[string]any{"rate_limits_available": true, "rate_limits": map[string]any{
		"five_hour": map[string]any{"utilization": five, "resets_at": reset},
		"seven_day": map[string]any{"utilization": seven, "resets_at": nil}}}
}

func TestReadTurnsTheAnswerIntoAReading(t *testing.T) {
	f := &fakeCLI{resp: answer(34.6, 39.0, "2026-10-09T05:00:00.5+00:00")}
	m, _ := meterWith(t, f, nil)
	r, authFailed := m.Read(context.Background(), "a")
	if r == nil || authFailed {
		t.Fatalf("read %v %v", r, authFailed)
	}
	if *r.Windows[0].Pct != 35 || *r.Windows[1].Pct != 39 || r.Windows[0].Minutes != 300 || r.Windows[1].Minutes != 10080 {
		t.Errorf("windows %+v %+v", r.Windows[0], r.Windows[1])
	}
	if r.Windows[0].Reset != time.Date(2026, 10, 9, 5, 0, 0, 0, time.UTC).Unix() || r.Windows[1].Reset != 0 {
		t.Errorf("resets %d %d", r.Windows[0].Reset, r.Windows[1].Reset)
	}
	if r.At != now || !f.closed {
		t.Errorf("at %d, closed %v", r.At, f.closed)
	}
}

func TestTheProcessGetsNoAccountSettingsAndNoSession(t *testing.T) {
	m, _ := meterWith(t, &fakeCLI{resp: answer(1.0, 1.0, "")}, nil)
	l, cleanup, err := m.launch("a")
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	if len(l.Args) != 2 || l.Args[0] != "--setting-sources" || l.Args[1] != "project" || l.SessionID != "" {
		t.Errorf("launch %+v", l)
	}
	if filepath.Base(l.ConfigDir) != ".claude" {
		t.Errorf("config dir %q", l.ConfigDir)
	}
}

func TestNoFigureIsNoReading(t *testing.T) {
	for name, resp := range map[string]map[string]any{
		"unavailable": {"rate_limits_available": false, "rate_limits": nil},
		"null":        {"rate_limits_available": true, "rate_limits": nil},
		"empty":       {"rate_limits_available": true, "rate_limits": map[string]any{}},
		"badreset":    answer(10.0, 10.0, "tomorrow"),
	} {
		m, _ := meterWith(t, &fakeCLI{resp: resp}, nil)
		if r, _ := m.Read(context.Background(), "a"); r != nil {
			t.Errorf("%s: got a reading %+v", name, r)
		}
	}
}

func TestARefusedLoginIsToldApartFromATimeout(t *testing.T) {
	m, _ := meterWith(t, &fakeCLI{err: errors.New("usage request failed: 401 Unauthorized")}, nil)
	if r, auth := m.Read(context.Background(), "a"); r != nil || !auth {
		t.Errorf("401: %v %v", r, auth)
	}
	m, _ = meterWith(t, &fakeCLI{err: errors.New("deadline exceeded")}, nil)
	if r, auth := m.Read(context.Background(), "a"); r != nil || auth {
		t.Errorf("timeout: %v %v", r, auth)
	}
}

func TestRunModelIsOneTurnAndReadsTheResultFrame(t *testing.T) {
	f := &fakeCLI{result: map[string]any{"type": "result", "result": "hello"}}
	m, _ := meterWith(t, f, nil)
	if ok, out := m.RunModel(context.Background(), "a", "hi", "haiku"); !ok || out != "hello" || f.sent != "hi" {
		t.Errorf("run %v %q sent %q", ok, out, f.sent)
	}
	f = &fakeCLI{result: map[string]any{"type": "result", "is_error": true, "result": "Not logged in"}}
	m, _ = meterWith(t, f, nil)
	if ok, out := m.RunModel(context.Background(), "a", "hi", "haiku"); ok || out != "Not logged in" {
		t.Errorf("run %v %q", ok, out)
	}
}

// TestLiveMeter asks a real CLI for an account's usage through the Meter. LIVE_CLAUDE_CONFIG names the config dir; the Meter reads it from
// <home>/accounts/<acct>/.claude, so a link to it stands in. LIVE_CLAUDE_CONFIG=<dir> go test -run TestLiveMeter -v ./internal/usagepoll
func TestLiveMeter(t *testing.T) {
	cfg := os.Getenv("LIVE_CLAUDE_CONFIG")
	if cfg == "" {
		t.Skip("LIVE_CLAUDE_CONFIG is not set")
	}
	m := liveMeter(t, cfg)
	c, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	r, auth := m.Read(c, "live")
	if r == nil {
		t.Fatalf("no reading (authFailed %v)", auth)
	}
	t.Logf("reading: %s resets %d / %d", Describe(r), r.Windows[0].Reset, r.Windows[1].Reset)
}

// TestLiveLoggedOutDirectory is the dead-login check that needs no credential: a config dir the CLI has no login in. The Meter must
// return no reading (and say what the CLI said), not hang or read as a quiet account.
func TestLiveLoggedOutDirectory(t *testing.T) {
	if os.Getenv("LIVE_CLAUDE_LOGGED_OUT") == "" {
		t.Skip("LIVE_CLAUDE_LOGGED_OUT is not set")
	}
	m := liveMeter(t, t.TempDir())
	c, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	r, auth := m.Read(c, "live")
	t.Logf("reading %v, authFailed %v", r, auth)
	if r != nil {
		t.Error("a logged-out directory gave a reading")
	}
}

func liveMeter(t *testing.T, cfg string) *Meter {
	t.Helper()
	path, err := exec.LookPath("claude")
	if err != nil {
		t.Fatal(err)
	}
	home := t.TempDir()
	if err := os.MkdirAll(paths.AccountDir(home, "live"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(cfg, filepath.Join(paths.AccountDir(home, "live"), ".claude")); err != nil {
		t.Fatal(err)
	}
	return &Meter{Home: home, Claude: path}
}
