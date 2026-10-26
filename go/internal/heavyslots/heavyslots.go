// Package heavyslots is where the machine's heavy-run slots are kept and how many there are (cmd/heavyslot holds them).
package heavyslots

import (
	"os"
	"path/filepath"
	"strconv"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/usage"
)

// Dir is the slots' directory: HANDLINE_HEAVY_DIR, else ~/.handline/heavy-slots.
func Dir() (string, error) {
	if d := os.Getenv("HANDLINE_HEAVY_DIR"); d != "" {
		return d, nil
	}
	home, err := os.UserHomeDir()
	return filepath.Join(home, ".handline", "heavy-slots"), err
}

// Default is how many runs may hold a slot at once when nothing says otherwise.
const Default = 2

// Of is the configured number (heavy_slots in config.yaml), Default when it is not a whole number of at least 1.
func Of(cfg usage.Config) int {
	switch n := cfg["heavy_slots"].(type) {
	case int:
		if n >= 1 {
			return n
		}
	case int64:
		if n >= 1 {
			return int(n)
		}
	}
	return Default
}

// Slots is how many runs may hold a slot at once: HANDLINE_HEAVY_SLOTS when set (an override), else heavy_slots in config.yaml, which a
// change of the setting reaches without a restart, else Default.
func Slots() int {
	if n, err := strconv.Atoi(os.Getenv("HANDLINE_HEAVY_SLOTS")); err == nil && n > 0 {
		return n
	}
	return Of(usage.LoadConfig(paths.Home()))
}
