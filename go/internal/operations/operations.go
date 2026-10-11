// Package operations is the set of long-running operations this process is running. A row in the operations table that says running has
// a thread behind it only while its id is here; one that is not was left by a daemon that stopped, and operation.get reports it failed.
package operations

import "sync"

var (
	mu   sync.Mutex
	live = map[string]bool{}
)

// Begin records that op is running here. It comes before the row exists, so nothing reads a running row of a live operation as dead.
func Begin(op string) {
	mu.Lock()
	live[op] = true
	mu.Unlock()
}

// End records that op's thread has returned, after its last row write.
func End(op string) {
	mu.Lock()
	delete(live, op)
	mu.Unlock()
}

// Live is whether op is running in this process.
func Live(op string) bool {
	mu.Lock()
	defer mu.Unlock()
	return live[op]
}
