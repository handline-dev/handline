// Package keepawake keeps the machine from idle sleep while the founder wants agents to run: caffeinate -i held by the daemon. Closing
// the lid still sleeps the machine, as caffeinate -i does not prevent that.
package keepawake

import (
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"sync"

	"github.com/handline/handline/go/internal/usage"
)

// Key is the config.yaml key, and the modes it takes: off (the default), on (held while the daemon runs) and working (held while an
// agent is mid-turn, released when the last one stops).
const (
	Key     = "keep_awake"
	Off     = "off"
	On      = "on"
	Working = "working"
)

// ModeOf is the configured mode; anything else reads as off.
func ModeOf(cfg usage.Config) string {
	switch s, _ := cfg[Key].(string); s {
	case On, Working:
		return s
	}
	return Off
}

// Lock is a held sleep lock: Release gives it up, and Alive says whether the process holding it still runs.
type Lock struct {
	Release func()
	Alive   func() bool
}

// Holder holds the sleep lock while Sync says it is wanted. Start takes the lock; nil runs caffeinate. A lock whose process died (it was
// killed) is dropped and taken again at the next Sync.
type Holder struct {
	Start func() (*Lock, error)
	Log   func(string)

	mu     sync.Mutex
	lock   *Lock
	failed bool
}

// Holding is whether the lock is held now.
func (h *Holder) Holding() bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.lock != nil && h.lock.Alive()
}

// Sync holds the lock when mode wants it (on, or working with an agent mid-turn) and releases it otherwise.
func (h *Holder) Sync(mode string, working bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	want := mode == On || (mode == Working && working)
	if h.lock != nil && !h.lock.Alive() {
		h.lock = nil
	}
	switch {
	case want && h.lock == nil && !h.failed:
		start := h.Start
		if start == nil {
			start = caffeinate
		}
		lock, err := start()
		if err != nil {
			h.failed = true // once, not at every tick
			if h.Log != nil {
				h.Log(fmt.Sprintf("keep-awake: not held: %v", err))
			}
			return
		}
		h.lock = lock
	case !want && h.lock != nil:
		h.lock.Release()
		h.lock = nil
	}
	if !want {
		h.failed = false
	}
}

// caffeinate holds the idle-sleep lock until released or until this daemon exits (-w), so a killed daemon leaves none behind.
func caffeinate() (*Lock, error) {
	if runtime.GOOS != "darwin" {
		return nil, fmt.Errorf("only macOS has caffeinate")
	}
	cmd := exec.Command("caffeinate", "-i", "-w", fmt.Sprint(os.Getpid()))
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	died := make(chan struct{})
	go func() { _ = cmd.Wait(); close(died) }()
	return &Lock{
		Release: func() { _ = cmd.Process.Kill(); <-died },
		Alive: func() bool {
			select {
			case <-died:
				return false
			default:
				return true
			}
		}}, nil
}
