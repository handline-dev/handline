// Package heavyslots is where the machine's heavy-run slots are kept and how many there are (cmd/heavyslot holds them).
package heavyslots

import (
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"syscall"

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

// LightSlots is how many runs of one test file may hold a light slot at once: HANDLINE_LIGHT_SLOTS, else 2.
func LightSlots() int {
	if n, err := strconv.Atoi(os.Getenv("HANDLINE_LIGHT_SLOTS")); err == nil && n > 0 {
		return n
	}
	return 2
}

// Held is how many heavy slots are held now: each slot-*.lock in Dir is opened read-only (never created) and a shared flock tried on
// it, the probe cmd/heavyslot's tickets use. A missing directory is 0; light slots are not counted.
func Held() int {
	dir, err := Dir()
	if err != nil {
		return 0
	}
	locks, _ := filepath.Glob(filepath.Join(dir, "slot-*.lock"))
	n := 0
	for _, path := range locks {
		f, err := os.Open(path)
		if err != nil {
			continue
		}
		if err := syscall.Flock(int(f.Fd()), syscall.LOCK_SH|syscall.LOCK_NB); errors.Is(err, syscall.EWOULDBLOCK) {
			n++
		}
		f.Close()
	}
	return n
}
