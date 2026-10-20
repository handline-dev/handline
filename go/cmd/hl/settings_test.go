package main

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/handline/handline/go/internal/protected"
)

// founderRig is a rig whose terminal holds the founder key.
func founderRig(t *testing.T) *rig {
	r := agentsRig(t)
	t.Setenv("HANDLINE_TEST_ROOT", r.root)
	dir := filepath.Join(protected.RootFor(r.home), "founder-keys")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "key"), []byte("k\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return r
}

const settingsGot = `{"values":{"park_timeout_s":600,"bg_notice_s":600,"bg_grace_s":300,"bg_check_s":600,"notice_digest_s":300,
	"ignore_cache_expiry":false},"overrides":{"park_timeout_s":0,"notice_digest_s":120,"ignore_cache_expiry":true}}`

func TestSettingsWriteThenPrintWhatTheDaemonHolds(t *testing.T) {
	r := founderRig(t)
	calls := r.daemon(map[string]string{"settings.get": settingsGot, "settings.set": `{"accepted":true,"said":"set"}`})
	cases := []struct {
		args []string
		out  string
		want []string
	}{
		{[]string{"notice-digest", "2m", "--agent", "b.two"}, "b.two: notices are told in one digest at its prompt, from 2m after " +
			"the first and never mid-turn (sooner with any message); a failure, block or FATAL at once\n",
			[]string{`settings.set {"agent":"b.two","changes":{"notice_digest_s":120}}`, `settings.get {"agent":"b.two"}`}},
		{[]string{"notice-digest"}, "default: notices are told in one digest at its prompt, from 5m after the first and never " +
			"mid-turn (sooner with any message); a failure, block or FATAL at once\n", []string{`settings.get {}`}},
		{[]string{"idle-background", "--grace", "10m"}, "default: asked which background work to stop every 10m while any runs\n" +
			"default: told to finish or stop its background work after 10m of nothing shown, and it is stopped 5m after that\n",
			[]string{`settings.set {"changes":{"bg_grace_s":600}}`, `settings.get {}`}},
	}
	seen := 0
	for _, c := range cases {
		out, errs, code := r.run(nil, c.args...)
		all := sent(calls())
		got := all[seen:]
		seen = len(all)
		if code != 0 || errs != "" || out != c.out || !reflect.DeepEqual(got, c.want) {
			t.Errorf("%v: exit %d, out %q, err %q\n got  %v\n want %v", c.args, code, out, errs, got, c.want)
		}
	}
}

func TestSettingsRefusalsSayErrorOnStderr(t *testing.T) {
	r := founderRig(t)
	calls := r.daemon(map[string]string{"settings.set": "error:config.yaml could not be written"})
	if _, errs, code := r.run(nil, "notice-digest", "--clear"); code != 1 || errs != "--clear only applies with --agent\n" {
		t.Errorf("clear: %q %d", errs, code)
	}
	if _, errs, code := r.run(nil, "notice-digest", "2m"); code != 1 || errs != "error: config.yaml could not be written\n" {
		t.Errorf("refused: %q %d", errs, code)
	}
	if got := sent(calls()); !reflect.DeepEqual(got, []string{`settings.set {"changes":{"notice_digest_s":120}}`}) {
		t.Fatalf("calls %v", got)
	}
}

func TestIdleBackgroundSaysNeverToldWhenIdleNeverCloses(t *testing.T) {
	r := founderRig(t)
	r.daemon(map[string]string{"settings.get": `{"values":{"park_timeout_s":0,"bg_notice_s":1,"bg_grace_s":300,"bg_check_s":600}}`})
	want := "default: asked which background work to stop every 10m while any runs\ndefault: never told - its idle window is 'never close'\n"
	if out, _, code := r.run(nil, "idle-background"); code != 0 || out != want {
		t.Errorf("%q %d", out, code)
	}
}

func TestSettingsAnAgentOrAKeylessTerminalAndWhatIsNotParsedAreRefused(t *testing.T) {
	keyless := agentsRig(t)
	keyless.daemon(nil)
	r := founderRig(t)
	calls := r.daemon(nil)
	cases := []struct {
		r    *rig
		env  []string
		args []string
	}{
		{keyless, nil, []string{"notice-digest"}},
		{r, asAgent, []string{"notice-digest", "2m"}},
		{r, nil, []string{"idle-background", "--agent", "b.two"}},
		{r, nil, []string{"notice-digest", "0m"}},
	}
	for _, c := range cases {
		out, _, code := c.r.run(c.env, c.args...)
		if code != 2 || out != "" {
			t.Errorf("%v: %q %d", c.args, out, code)
		}
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("calls %v", got)
	}
}
