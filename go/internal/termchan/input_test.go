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

// holdIdle puts casey's hold on ann, last typed an hour before the rig's clock.
func holdIdle(t *testing.T, r rig) {
	t.Helper()
	at := r.e.now().Unix() - 3600
	if _, err := r.e.DB.Exec("INSERT INTO input_holders(agent, kind, name, since, last_typed) VALUES('ann', 'person', 'casey', ?, ?)", at, at); err != nil {
		t.Fatal(err)
	}
}

func holderPayloads(t *testing.T, r rig) []string {
	t.Helper()
	rows, err := r.e.DB.Query("SELECT payload FROM events WHERE kind = 'input_holder' AND agent = 'ann' ORDER BY id")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var p string
		rows.Scan(&p)
		out = append(out, p)
	}
	return out
}

const daemonHolds = `{"holders": [{"kind": "daemon", "name": "daemon", "since": null}]}`

func TestAnIdleHoldFoundByAnyReadIsRecordedOnceAndTold(t *testing.T) {
	reads := map[string]func(rig){
		"the holder list an attach reads": func(r rig) { r.e.holders("ann") },
		"the gate of a take":              func(r rig) { r.e.take(person("robin"), map[string]any{"agent": "ann"}) },
		"a release":                       func(r rig) { r.e.release(person("robin"), map[string]any{"agent": "ann"}) },
	}
	for name, read := range reads {
		t.Run(name, func(t *testing.T) {
			r := newRig(t)
			r.agent(t)
			holdIdle(t, r)
			read(r)
			got := holderPayloads(t, r)
			if name == "the gate of a take" { // the take that follows is a change of its own
				if len(got) != 2 || got[0] != daemonHolds {
					t.Fatalf("%v", got)
				}
				return
			}
			if len(got) != 1 || got[0] != daemonHolds {
				t.Fatalf("%v", got)
			}
			r.e.holders("ann") // the hold is gone: reading again records nothing
			if len(holderPayloads(t, r)) != 1 {
				t.Fatal("a second read told again")
			}
		})
	}
}
