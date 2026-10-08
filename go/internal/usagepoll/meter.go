// Package usagepoll asks accounts where they stand and keeps their readings fresh (handline/daemon/usage_poll.py, vendors/claude_meter.py).
// The daemon reads no credential: a short-lived CLI process asks with its own login (the get_usage control request), and a dead token is
// earned back with one real turn on the same kind of process.
package usagepoll

import (
	"context"
	"fmt"
	"math"
	"os"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/usage"
)

const (
	FiveHourMinutes = 300
	SevenDayMinutes = 10080
	// FoldTimeout is how long the one real turn of a recovery may take (fold.FOLD_TIMEOUT_S).
	FoldTimeout = 180 * time.Second
	openTimeout = 60 * time.Second
	askTimeout  = 60 * time.Second
	// RecoverModel and RecoverPrompt are the turn that makes the CLI renew its token: spent, never read.
	RecoverModel  = "haiku"
	RecoverPrompt = "hi"
)

// CLI is the part of a structured session the meter uses.
type CLI interface {
	GetUsage(ctx context.Context) (map[string]any, error)
	Send(op, text string, d structured.Delivery) (structured.Receipt, error)
	Close() error
}

// OpenFunc starts a CLI. The default is structured.Open.
type OpenFunc func(ctx context.Context, l structured.Launch, h structured.Handlers) (CLI, error)

// Meter asks a CLI process, one per question.
type Meter struct {
	Home string
	// Claude is the CLI's path.
	Claude string
	Open   OpenFunc
	Now    func() time.Time
}

func (m *Meter) open(ctx context.Context, l structured.Launch, h structured.Handlers) (CLI, error) {
	if m.Open != nil {
		return m.Open(ctx, l, h)
	}
	return structured.Open(ctx, l, h, openTimeout)
}

func (m *Meter) now() time.Time {
	if m.Now != nil {
		return m.Now()
	}
	return time.Now()
}

// launch is the process for a question with no session in it: an empty working directory, and only project settings, so the account's
// hooks (which report to the daemon as an agent) and allow rules do not run in it. The login is not in settings.json.
func (m *Meter) launch(acct string) (l structured.Launch, cleanup func(), err error) {
	dir, err := team.AccountConfigDir(m.Home, acct)
	if err != nil {
		return l, nil, err
	}
	cwd, err := os.MkdirTemp("", "usage-")
	if err != nil {
		return l, nil, err
	}
	return structured.Launch{Path: m.Claude, Cwd: cwd, ConfigDir: dir, Args: []string{"--setting-sources", "project"}},
		func() { os.RemoveAll(cwd) }, nil
}

// Read asks the account's CLI for its plan limits. A nil reading is "could not look", never "nothing spent"; authFailed is true when the
// CLI said its login was refused, the one failure recovery needs to tell from a timeout.
func (m *Meter) Read(ctx context.Context, acct string) (r *usage.Reading, authFailed bool) {
	l, cleanup, err := m.launch(acct)
	if err != nil {
		return nil, false
	}
	defer cleanup()
	c, cancel := context.WithTimeout(ctx, askTimeout+openTimeout)
	defer cancel()
	s, err := m.open(c, l, structured.Handlers{})
	if err != nil {
		return nil, refused(err)
	}
	defer s.Close()
	ctx2, cancel2 := context.WithTimeout(c, askTimeout)
	defer cancel2()
	resp, err := s.GetUsage(ctx2)
	if err != nil {
		return nil, refused(err)
	}
	r, err = parseUsage(acct, resp, m.now().Unix())
	if err != nil {
		return nil, refused(err)
	}
	return r, false
}

// refused is whether the CLI's own words say its login was refused.
func refused(err error) bool {
	s := strings.ToLower(err.Error())
	for _, w := range []string{"401", "403", "unauthorized", "forbidden"} {
		if strings.Contains(s, w) {
			return true
		}
	}
	return false
}

// RunModel spends one real turn on the account (fold.run_model): the one thing that makes the CLI renew the token it is about to
// spend. ok is whether the turn completed without an error.
func (m *Meter) RunModel(ctx context.Context, acct, prompt, model string) (ok bool, out string) {
	l, cleanup, err := m.launch(acct)
	if err != nil {
		return false, err.Error()
	}
	defer cleanup()
	l.Model = model
	done := make(chan map[string]any, 1)
	c, cancel := context.WithTimeout(ctx, FoldTimeout)
	defer cancel()
	s, err := m.open(c, l, structured.Handlers{OnEvent: func(ev map[string]any) {
		if ev["type"] == "result" {
			select {
			case done <- ev:
			default:
			}
		}
	}})
	if err != nil {
		return false, err.Error()
	}
	defer s.Close()
	if _, err := s.Send("recover", prompt, structured.Queue); err != nil {
		return false, err.Error()
	}
	select {
	case ev := <-done:
		text, _ := ev["result"].(string)
		if bad, _ := ev["is_error"].(bool); bad {
			return false, oneLine(text)
		}
		return true, strings.TrimSpace(text)
	case <-c.Done():
		return false, fmt.Sprintf("fold timed out after %ds", int(FoldTimeout.Seconds()))
	}
}

func oneLine(s string) string {
	s = strings.Join(strings.Fields(s), " ")
	if len(s) > 500 {
		s = s[:500]
	}
	return s
}

// parseUsage reads a get_usage answer. The two windows are the plan's five-hour and seven-day limits. No limits, or no figure in any
// window, is no reading: an account is never read as quiet because the answer was empty.
func parseUsage(acct string, resp map[string]any, at int64) (*usage.Reading, error) {
	if avail, _ := resp["rate_limits_available"].(bool); !avail {
		return nil, fmt.Errorf("the plan limits were not available")
	}
	limits, _ := resp["rate_limits"].(map[string]any)
	if limits == nil {
		return nil, fmt.Errorf("the answer has no rate_limits")
	}
	r := &usage.Reading{Account: acct, At: at}
	any_ := false
	for _, w := range []struct {
		key     string
		minutes int
	}{{"five_hour", FiveHourMinutes}, {"seven_day", SevenDayMinutes}} {
		m, _ := limits[w.key].(map[string]any)
		rw := usage.ReadingWindow{Minutes: w.minutes}
		if v, ok := m["utilization"].(float64); ok && !math.IsNaN(v) {
			p := int(math.Max(0, math.Min(100, math.Floor(v+0.5))))
			rw.Pct = &p
			any_ = true
		}
		reset, err := resetOf(m["resets_at"])
		if err != nil {
			return nil, fmt.Errorf("%s: %w", w.key, err)
		}
		rw.Reset = reset
		r.Windows = append(r.Windows, rw)
	}
	if !any_ {
		return nil, fmt.Errorf("the answer has no figure in either window")
	}
	return r, nil
}

// resetOf is a window's resets_at as a unix second. Absent is 0: a window nothing has been spent in names no reset. A value that is
// there and unreadable is an error, not an untouched window.
func resetOf(v any) (int64, error) {
	switch x := v.(type) {
	case nil:
		return 0, nil
	case string:
		if strings.TrimSpace(x) == "" {
			return 0, nil
		}
		t, err := time.Parse(time.RFC3339Nano, x)
		if err != nil {
			return 0, fmt.Errorf("resets_at %q is not a time", x)
		}
		return t.Unix(), nil
	case float64:
		if x > 1e12 { // milliseconds
			x /= 1000
		}
		return int64(x), nil
	}
	return 0, fmt.Errorf("resets_at has type %T", v)
}
