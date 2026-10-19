package main

import (
	"testing"
)

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
