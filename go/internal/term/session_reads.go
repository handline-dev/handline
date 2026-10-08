package term

import "time"

// ExitOverWorkTitle is the title of the exit dialog over background work, lower-cased.
const ExitOverWorkTitle = exitOverWorkTitle

// ScreenCacheTTL is how old a cached screen read may be.
const ScreenCacheTTL = 5 * time.Second

type cachedBlocker struct {
	at time.Time
	b  *Blocker
}

// ScreenBlockerCached is a cached read of handle's screen, at most ScreenCacheTTL old. notBefore refuses a cached read older than
// that instant: a read taken before a fact existed cannot answer a question about it. The zero time accepts any read in the TTL.
func (t *Term) ScreenBlockerCached(handle string, notBefore time.Time) *Blocker {
	now := time.Now()
	t.cacheMu.Lock()
	seen, ok := t.cache[handle]
	t.cacheMu.Unlock()
	if ok && seen.at.After(notBefore) && now.Sub(seen.at) < ScreenCacheTTL {
		return seen.b
	}
	b := t.ScreenBlocker(handle)
	t.cacheMu.Lock()
	if t.cache == nil {
		t.cache = map[string]cachedBlocker{}
	}
	t.cache[handle] = cachedBlocker{now, b}
	t.cacheMu.Unlock()
	return b
}

// StopWorkAndExit presses Enter on the exit dialog over background work, when StoppingKey allows; never on a truncated read.
func (t *Term) StopWorkAndExit(handle string) (outcome, title, why string) {
	return t.pressDialog(handle, StoppingKey, "Enter", false)
}

// AcceptDialog presses the vendor's one-time yes on its prompt to run tool, if and only if the screen just read is exactly that
// prompt.
func (t *Term) AcceptDialog(handle, tool string) (outcome, title, why string) {
	drv := t.driver(handle)
	return t.pressDialog(handle, func(d *Dialog) (string, string) { return drv.AcceptingKey(d, tool) }, "Enter", true)
}

// VendorOf is the name of the vendor whose wording reads handle's screen.
func (t *Term) VendorOf(handle string) string { return t.driver(handle).Name() }

// Available is whether the host's own tool can be run.
func (t *Term) Available() bool { return t.Backend.Available() }
