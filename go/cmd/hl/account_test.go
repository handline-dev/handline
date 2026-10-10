package main

import (
	"reflect"
	"strings"
	"testing"
)

const accountsGot = `{"accounts":[{"name":"alpha","cap":null,"cap_effective":2},{"name":"beta","cap":3,"cap_effective":3}]}`

func TestAccountCapShowsAndSetsThroughTheDaemon(t *testing.T) {
	r := founderRig(t)
	calls := r.daemon(map[string]string{"accounts.list": accountsGot, "account.set": `{"accepted":true,"said":"set"}`})
	if out, _, code := r.run(asAgent, "account", "cap", "beta"); code != 0 || out != "beta: at most 3 agents mid-turn at once\n" {
		t.Errorf("show: %q %d", out, code)
	}
	if out, _, code := r.run(nil, "account", "cap", "alpha", "4"); code != 0 || out != "alpha: at most 4 agents mid-turn at once\n" {
		t.Errorf("set: %q %d", out, code)
	}
	if got := sent(calls()); !reflect.DeepEqual(got, []string{`accounts.list {}`, `accounts.list {}`, `account.set {"account":"alpha","cap":4}`}) {
		t.Fatalf("calls %v", got)
	}
}

func TestAccountCapRefusedSaysWhyOnStderr(t *testing.T) {
	r := founderRig(t)
	r.daemon(map[string]string{"accounts.list": accountsGot, "account.set": "error:config.yaml could not be written"})
	if out, errs, code := r.run(nil, "account", "cap", "alpha", "4"); code != 1 || out != "" || errs != "config.yaml could not be written\n" {
		t.Errorf("%q %q %d", out, errs, code)
	}
}

func TestAccountCapIsPythonsForWhatTheDaemonDoesNotSettle(t *testing.T) {
	r := founderRig(t)
	r.daemon(map[string]string{"accounts.list": accountsGot})
	for name, c := range map[string]struct {
		env  []string
		args []string
	}{
		"an account it does not list": {nil, []string{"account", "cap", "gamma"}},
		"a cap below one":             {nil, []string{"account", "cap", "alpha", "0"}},
		"set from an agent's session": {asAgent, []string{"account", "cap", "alpha", "4"}},
		"another account command":     {nil, []string{"account", "list"}},
		"not a number":                {nil, []string{"account", "cap", "alpha", "x"}},
	} {
		out, _, code := r.run(c.env, c.args...)
		if code != 0 || !strings.HasPrefix(out, "python ") || !strings.HasSuffix(out, " "+strings.Join(c.args, " ")+"\n") {
			t.Errorf("%s: %q %d", name, out, code)
		}
	}
}
