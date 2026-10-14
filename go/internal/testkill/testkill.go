// Package testkill tears down what a test starts: at the test's end, pass or fail, and when the test binary is interrupted first, which
// t.Cleanup alone does not cover (an interrupted binary runs no cleanups).
package testkill

import (
	"os"
	"os/signal"
	"sync"
	"syscall"
	"testing"
	"time"
)

var (
	mu    sync.Mutex
	kills = map[int]func(){}
	next  int
	watch sync.Once
)

// OnEnd runs kill once: when t ends, or when the test binary gets SIGINT, SIGTERM or SIGHUP before that.
func OnEnd(t testing.TB, kill func()) {
	watch.Do(onSignal)
	mu.Lock()
	id := next
	next++
	kills[id] = kill
	mu.Unlock()
	t.Cleanup(func() { take(id)() })
}

func take(id int) func() {
	mu.Lock()
	defer mu.Unlock()
	k, ok := kills[id]
	delete(kills, id)
	if !ok {
		return func() {}
	}
	return k
}

// onSignal kills everything still tracked, then lets the signal end the binary as it would have.
func onSignal() {
	c := make(chan os.Signal, 1)
	signal.Notify(c, syscall.SIGINT, syscall.SIGTERM, syscall.SIGHUP)
	go func() {
		sig := (<-c).(syscall.Signal)
		mu.Lock()
		ids := make([]int, 0, len(kills))
		for id := range kills {
			ids = append(ids, id)
		}
		mu.Unlock()
		for _, id := range ids {
			take(id)()
		}
		signal.Reset(sig)
		syscall.Kill(os.Getpid(), sig)
	}()
}

// ExitWithParent is for a process a test binary starts as itself (a fake CLI): once that binary is gone, by an interrupt, a timeout or a
// test that never stopped it, this process kills its own process group, so nothing it started outlives the run.
func ExitWithParent() {
	parent := os.Getppid()
	go func() {
		for os.Getppid() == parent {
			time.Sleep(200 * time.Millisecond)
		}
		if syscall.Getpgrp() == os.Getpid() { // only a group of its own: never the group of whoever ran the tests
			syscall.Kill(0, syscall.SIGKILL)
		}
		os.Exit(1)
	}()
}
