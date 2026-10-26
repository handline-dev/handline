package keepawake

import (
	"testing"

	"github.com/handline/handline/go/internal/usage"
)

func TestModeOfReadsOffForAnythingUnknown(t *testing.T) {
	for cfg, want := range map[string]string{"on": On, "working": Working, "off": Off, "always": Off, "": Off} {
		if got := ModeOf(usage.Config{Key: cfg}); got != want {
			t.Errorf("%q: %s, want %s", cfg, got, want)
		}
	}
	if ModeOf(usage.Config{}) != Off || ModeOf(usage.Config{Key: true}) != Off {
		t.Error("unset or not text is off")
	}
}

func TestTheLockIsHeldPerModeAndReleasedWhenNoLongerWanted(t *testing.T) {
	held, released := 0, 0
	h := &Holder{Start: func() (func(), error) { held++; return func() { released++ }, nil }}
	h.Sync(Off, true)
	h.Sync(Working, false)
	if held != 0 || h.Holding() {
		t.Fatal("off, or working with no agent mid-turn, holds nothing")
	}
	h.Sync(Working, true)
	h.Sync(Working, true)
	if held != 1 || !h.Holding() {
		t.Fatalf("held once while an agent works: %d", held)
	}
	h.Sync(Working, false)
	if released != 1 || h.Holding() {
		t.Fatal("released when the last agent stops")
	}
	h.Sync(On, false)
	if held != 2 || !h.Holding() {
		t.Fatal("on holds with nothing running")
	}
	h.Sync(Off, false)
	if released != 2 {
		t.Fatal("off releases")
	}
}

func TestAStartThatFailsIsTriedAgainOnlyAfterTheModeChanges(t *testing.T) {
	tries := 0
	h := &Holder{Start: func() (func(), error) { tries++; return nil, errNo }}
	for range 3 {
		h.Sync(On, false)
	}
	if tries != 1 {
		t.Fatalf("tried %d times", tries)
	}
	h.Sync(Off, false)
	h.Sync(On, false)
	if tries != 2 {
		t.Fatalf("tried again after off: %d", tries)
	}
}

type noErr string

func (e noErr) Error() string { return string(e) }

const errNo = noErr("no caffeinate")
