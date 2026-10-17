package reads

import (
	"slices"
	"testing"

	"github.com/handline/handline/go/internal/claude"
)

func TestTheSettingsPageReadsTheHooksTheLaunchInstalls(t *testing.T) {
	a, b := slices.Sorted(slices.Values(hooksWanted)), slices.Sorted(slices.Values(claude.Wanted))
	if !slices.Equal(a, b) {
		t.Fatalf("settings reads %v, the launch installs %v", hooksWanted, claude.Wanted)
	}
}
