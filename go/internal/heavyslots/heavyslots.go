// Package heavyslots is where the machine's heavy-run slots are kept and how many there are (cmd/heavyslot holds them).
package heavyslots

import (
	"os"
	"path/filepath"
	"strconv"
)

// Dir is the slots' directory: HANDLINE_HEAVY_DIR, else ~/.handline/heavy-slots.
func Dir() (string, error) {
	if d := os.Getenv("HANDLINE_HEAVY_DIR"); d != "" {
		return d, nil
	}
	home, err := os.UserHomeDir()
	return filepath.Join(home, ".handline", "heavy-slots"), err
}

// Slots is how many runs may hold a slot at once: HANDLINE_HEAVY_SLOTS, default 1.
func Slots() int {
	if n, err := strconv.Atoi(os.Getenv("HANDLINE_HEAVY_SLOTS")); err == nil && n > 0 {
		return n
	}
	return 1
}
