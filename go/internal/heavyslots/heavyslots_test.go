package heavyslots

import (
	"testing"

	"github.com/handline/handline/go/internal/usage"
)

func TestTheConfiguredSlotsAreUsedAndAnythingElseIsTheDefault(t *testing.T) {
	for _, c := range []struct {
		v    any
		want int
	}{{3, 3}, {int64(4), 4}, {1, 1}, {0, Default}, {-2, Default}, {"3", Default}, {nil, Default}} {
		if got := Of(usage.Config{"heavy_slots": c.v}); got != c.want {
			t.Errorf("%v: %d, want %d", c.v, got, c.want)
		}
	}
}

func TestTheEnvironmentOverridesTheConfiguredSlots(t *testing.T) {
	t.Setenv("HANDLINE_HEAVY_SLOTS", "5")
	if Slots() != 5 {
		t.Fatalf("%d", Slots())
	}
}
