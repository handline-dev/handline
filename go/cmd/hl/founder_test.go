package main

import (
	"reflect"
	"testing"
)

func TestDeviceListRevokeAndPairGoThroughTheDaemon(t *testing.T) {
	r := founderRig(t)
	calls := r.daemon(map[string]string{
		"devices.list":  `{"devices":[{"id":"dev_1","name":"phone","connected":true,"last_seen":"2026-10-17T10:00:00Z"},{"id":"dev_22","name":"tablet","connected":false,"last_seen":null}]}`,
		"device.revoke": `{"accepted":true}`,
		"device.pair":   `{"code":"123456","expires_at":"2026-10-17T10:05:00Z","uri":"handline://pair?host=1.2.3.4","fingerprint":null}`,
	})
	want := "dev_1   phone             connected  last seen 2026-10-17T10:00:00Z\ndev_22  tablet            offline    last seen never\n"
	if out, _, code := r.run(nil, "device", "list"); code != 0 || out != want {
		t.Errorf("list: %q %d", out, code)
	}
	if out, _, code := r.run(nil, "device", "revoke", "dev_1"); code != 0 || out != "device dev_1 revoked; its connections are closed\n" {
		t.Errorf("revoke: %q %d", out, code)
	}
	out, _, code := r.run(nil, "device", "pair", "--name", "watch")
	if code != 0 || out != "code: 123456   (one time, expires 2026-10-17T10:05:00Z)\nuri: handline://pair?host=1.2.3.4\naddress: 1.2.3.4\nfingerprint: none (the gateway runs plain on the Tailscale address; the tailnet encrypts it)\n" {
		t.Errorf("pair: %q %d", out, code)
	}
	if got := sent(calls()); !reflect.DeepEqual(got, []string{`devices.list {}`, `device.revoke {"device":"dev_1"}`, `device.pair {"name":"watch"}`}) {
		t.Fatalf("calls %v", got)
	}
}

func TestCancelIsRefusedToAnAgentAndToAScriptWithNoTerminal(t *testing.T) {
	r := founderRig(t)
	calls := r.daemon(map[string]string{"request.cancel": `{"ok":true,"plan":"would cancel M7","said":"cancelled M7"}`})
	for name, env := range map[string][]string{"an agent": asAgent, "no terminal": nil} {
		if out, _, code := r.run(env, "cancel", "7", "--yes"); code != 1 || out == "" {
			t.Errorf("%s cancelled: %q %d", name, out, code)
		}
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("calls %v", got)
	}
}

func TestServiceInstallAndUninstallRefuseAScratchHomeWithoutARehearsalLabel(t *testing.T) {
	r := newRig(t)
	for _, verb := range []string{"install", "uninstall"} {
		out, _, code := r.run(nil, "service", verb)
		if code != 1 || out == "" {
			t.Errorf("%s: %q %d", verb, out, code)
		}
	}
	if _, errs, code := r.run(nil, "service", "use", "go"); code != 2 || errs == "" {
		t.Errorf("service use: %q %d", errs, code)
	}
}
