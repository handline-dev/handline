package main

import (
	"reflect"
	"testing"
)

// device pair makes a code through the daemon; listing and revoking are the app's, so they are not understood here.
func TestDevicePairGoesThroughTheDaemonAndNothingElseIsADeviceVerb(t *testing.T) {
	r := founderRig(t)
	calls := r.daemon(map[string]string{
		"device.pair": `{"code":"123456","expires_at":"2026-10-17T10:05:00Z","uri":"handline://pair?host=1.2.3.4","fingerprint":null}`,
	})
	out, _, code := r.run(nil, "device", "pair", "--name", "watch")
	if code != 0 || out != "code: 123456   (one time, expires 2026-10-17T10:05:00Z)\nuri: handline://pair?host=1.2.3.4\naddress: 1.2.3.4\nfingerprint: none (the gateway runs plain on the Tailscale address; the tailnet encrypts it)\n" {
		t.Errorf("pair: %q %d", out, code)
	}
	for _, args := range [][]string{{"device", "list"}, {"device", "revoke", "dev_1"}} {
		if _, _, code := r.run(nil, args...); code != 2 {
			t.Errorf("%v: %d", args, code)
		}
	}
	if got := sent(calls()); !reflect.DeepEqual(got, []string{`device.pair {"name":"watch"}`}) {
		t.Fatalf("calls %v", got)
	}
}

func TestTeamSealGoesThroughTheDaemon(t *testing.T) {
	r := founderRig(t)
	calls := r.daemon(map[string]string{"team.seal": `{"accepted":true,"said":"sealed team.yaml and 2 agent files"}`})
	if out, _, code := r.run(nil, "team", "seal"); code != 0 || out != "sealed team.yaml and 2 agent files\n" {
		t.Fatalf("%q %d", out, code)
	}
	if got := sent(calls()); !reflect.DeepEqual(got, []string{`team.seal {}`}) {
		t.Fatalf("calls %v", got)
	}
}
