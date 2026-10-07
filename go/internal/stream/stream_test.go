package stream

import (
	"encoding/json"
	"sync"
	"testing"
)

func TestASlowClientGetsAGapAndIsHungUp(t *testing.T) {
	var mu sync.Mutex
	var sent [][]byte
	hung := make(chan struct{})
	s := &sub{
		send:   func(b []byte) bool { mu.Lock(); sent = append(sent, b); mu.Unlock(); return true },
		hangUp: func() { close(hung) }}
	s.cond = sync.NewCond(&s.mu)
	for i := 0; i <= MaxQueue; i++ {
		s.offer([]byte(`{"n":1}`))
	}
	done := make(chan struct{})
	go func() { s.run(); close(done) }()
	<-done
	<-hung
	var g struct {
		Method string
		Params struct{ Reason string }
	}
	if len(sent) != 1 || json.Unmarshal(sent[0], &g) != nil || g.Method != "gap" || g.Params.Reason != "slow_client" {
		t.Fatalf("%q", sent)
	}
}
