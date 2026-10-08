package term

import (
	"strings"
	"testing"
	"time"
)

var exitOverWork = []string{"earlier output", strings.Repeat("▔", 20), " Background work is running", "",
	" ❯ 1. Exit and stop tasks", "   2. Stay", "", " Enter to confirm · Esc to cancel"}

func TestStopWorkAndExitPressesEnterOnlyOnTheExitDialog(t *testing.T) {
	h := &fakeHost{screenAt: func(n int) []string {
		if n == 0 {
			return exitOverWork
		}
		return cleared
	}}
	outcome, title, why := newTerm(h).StopWorkAndExit("h")
	if outcome != Cleared || title != "Background work is running" || why != "" || len(h.sent) != 1 || h.sent[0] != "/true" {
		t.Fatalf("%q %q %q sent %v", outcome, title, why, h.sent)
	}

	// The wrong option highlighted: nothing is pressed.
	other := append([]string{}, exitOverWork...)
	other[4], other[5] = " ❯ 1. Stay", "   2. Exit and stop tasks"
	h = &fakeHost{screenAt: func(int) []string { return other }}
	outcome, _, why = newTerm(h).StopWorkAndExit("h")
	if outcome != NotPressed || !strings.Contains(why, "not 'Exit and stop tasks'") || len(h.sent) != 0 {
		t.Fatalf("%q %q sent %v", outcome, why, h.sent)
	}

	h = &fakeHost{screenAt: func(int) []string { return permissionPrompt }}
	if outcome, _, why = newTerm(h).StopWorkAndExit("h"); outcome != NotPressed || len(h.sent) != 0 {
		t.Fatalf("another dialog: %q %q sent %v", outcome, why, h.sent)
	}
}

func TestAcceptDialogPressesOnlyWhatTheVendorAccepts(t *testing.T) {
	h := &fakeHost{screenAt: func(int) []string { return permissionPrompt }}
	// claude takes no tool: the key is the driver's to name, and it names none.
	if outcome, _, _ := newTerm(h).AcceptDialog("h", "Bash"); outcome != NotPressed || len(h.sent) != 0 {
		t.Fatalf("outcome %q sent %v", outcome, h.sent)
	}
	if got := newTerm(h).VendorOf("h"); got != "claude" {
		t.Errorf("vendor %q", got)
	}
}

type countingHost struct {
	fakeHost
	reads int
}

func (c *countingHost) ReadScreen(h string, n int) *Screen {
	c.reads++
	return c.fakeHost.ReadScreen(h, n)
}

func TestAScreenReadIsCachedAndANewerOneIsAskedForByNotBefore(t *testing.T) {
	h := &countingHost{fakeHost: fakeHost{screenAt: func(int) []string { return exitOverWork }}}
	tm := newTerm(&h.fakeHost)
	tm.Backend = h
	first := tm.ScreenBlockerCached("h", time.Time{})
	if first == nil || first.Kind != Modal || h.reads != 1 {
		t.Fatalf("first read %+v reads %d", first, h.reads)
	}
	tm.ScreenBlockerCached("h", time.Time{})
	tm.ScreenBlockerCached("h", time.Now().Add(-time.Hour))
	if h.reads != 1 {
		t.Fatalf("a cached read within the TTL read again: %d", h.reads)
	}
	// A fact that began after the cached read cannot be answered by it.
	tm.ScreenBlockerCached("h", time.Now().Add(time.Hour))
	if h.reads != 2 {
		t.Fatalf("a read older than notBefore answered: %d", h.reads)
	}
	// Another handle, another Term: separate entries.
	tm.ScreenBlockerCached("other", time.Time{})
	if h.reads != 3 {
		t.Fatalf("reads %d", h.reads)
	}
}

func TestAvailableIsTheHosts(t *testing.T) {
	if !newTerm(&fakeHost{}).Available() {
		t.Fatal("the fake host is available")
	}
}
