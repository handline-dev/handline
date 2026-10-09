package termchan

import (
	"testing"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

func device(name string) *rpc.Conn {
	return &rpc.Conn{Caller: rpc.Caller{Role: "member", Info: map[string]any{"kind": "device", "name": name, "device": "d1"}}}
}

func holderEvents(t *testing.T, r rig) int {
	t.Helper()
	var n int
	if err := r.e.DB.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'input_holder'").Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func TestTakeHoldsTheInputAndRecordsIt(t *testing.T) {
	r := newRig(t)
	r.agent(t)
	got, err := r.e.take(person("casey"), map[string]any{"agent": "ann"})
	if err != nil {
		t.Fatal(err)
	}
	m := got.(map[string]any)
	if m["accepted"] != true || m["said"] != "ann: you hold its input" || m["holder"].(map[string]any)["name"] != "casey" || m["holder"].(map[string]any)["kind"] != "person" {
		t.Fatalf("%v", m)
	}
	if holderEvents(t, r) != 1 {
		t.Fatal("a take is an event")
	}
	if _, err := r.e.take(person("casey"), map[string]any{"agent": "ann"}); err != nil { // the holder takes again: allowed
		t.Fatal(err)
	}
}

func TestADeviceHoldsAsADevice(t *testing.T) {
	r := newRig(t)
	r.agent(t)
	got, err := r.e.take(device("phone"), map[string]any{"agent": "ann"})
	if err != nil || got.(map[string]any)["holder"].(map[string]any)["kind"] != "device" {
		t.Fatalf("%v %v", got, err)
	}
}

func TestTakeAndReleaseAreRefusedWhileSomeoneElseHoldsIt(t *testing.T) {
	r := newRig(t)
	r.agent(t)
	if _, err := r.e.take(person("casey"), map[string]any{"agent": "ann"}); err != nil {
		t.Fatal(err)
	}
	_, err := r.e.take(person("robin"), map[string]any{"agent": "ann"})
	if gate(t, err) != "input_holder" || err.(*rpc.Error).Message != "ann's terminal is held by casey" {
		t.Fatalf("%v", err)
	}
	_, err = r.e.release(person("robin"), map[string]any{"agent": "ann"})
	if gate(t, err) != "input_holder" {
		t.Fatalf("%v", err)
	}
	if holderEvents(t, r) != 1 {
		t.Fatal("a refusal records nothing")
	}
}

func TestReleaseGivesTheInputBackAndWhenTheDaemonHasItSaysSo(t *testing.T) {
	r := newRig(t)
	r.agent(t)
	got, _ := r.e.release(person("casey"), map[string]any{"agent": "ann"})
	if got.(map[string]any)["said"] != "ann: the daemon already holds its input" || holderEvents(t, r) != 0 {
		t.Fatalf("%v", got)
	}
	r.e.take(person("casey"), map[string]any{"agent": "ann"})
	got, err := r.e.release(person("casey"), map[string]any{"agent": "ann"})
	if err != nil || got.(map[string]any)["said"] != "ann: its input is the daemon's again" || holderEvents(t, r) != 2 {
		t.Fatalf("%v %v", got, err)
	}
	if h, _ := r.e.holders("ann"); h[0]["kind"] != "daemon" {
		t.Fatalf("%v", h)
	}
}

func TestAnIdleHoldIsLostSoAnotherCanTake(t *testing.T) {
	r := newRig(t)
	r.agent(t)
	r.e.take(person("casey"), map[string]any{"agent": "ann"})
	r.e.Now = func() time.Time { return time.Unix(1_000_000+600, 0) }
	if _, err := r.e.take(person("robin"), map[string]any{"agent": "ann"}); err != nil {
		t.Fatal(err)
	}
}

func TestTakeNeedsAnAgentThatExists(t *testing.T) {
	r := newRig(t)
	for _, p := range []map[string]any{{}, {"agent": 3}, {"agent": "nobody.here"}} {
		if _, err := r.e.take(person("casey"), p); err == nil {
			t.Fatalf("%v", p)
		}
		if _, err := r.e.release(person("casey"), p); err == nil {
			t.Fatalf("%v", p)
		}
	}
}

func TestAStructuredAgentHasNoInputToHold(t *testing.T) {
	r := newRig(t)
	r.agent(t)
	r.e.Structured = func(a string) bool { return a == "ann" }
	_, err := r.e.take(person("casey"), map[string]any{"agent": "ann"})
	if gate(t, err) != "unsupported" {
		t.Fatalf("%v", err)
	}
	_, err = r.e.release(person("casey"), map[string]any{"agent": "ann"})
	if gate(t, err) != "unsupported" || holderEvents(t, r) != 0 {
		t.Fatalf("%v", err)
	}
}
