package main

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
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

const settingsGot = `{"values":{"park_timeout_s":600,"bg_notice_s":600,"bg_grace_s":300,"bg_check_s":600,"notice_digest_s":900,
	"ignore_cache_expiry":false,"auto_accept_dialogs":true},"overrides":{"park_timeout_s":0,"notice_digest_s":120,"ignore_cache_expiry":true}}`

func TestSettingsWriteThenPrintWhatTheDaemonHolds(t *testing.T) {
	r := founderRig(t)
	calls := r.daemon(map[string]string{"settings.get": settingsGot, "settings.set": `{"accepted":true,"said":"set"}`})
	cases := []struct {
		args []string
		out  string
		want []string
	}{
		{[]string{"idle-timeout"}, "default: closes after 600s idle\n", []string{`settings.get {}`}},
		{[]string{"idle-timeout", "never"}, "default: closes after 600s idle\n",
			[]string{`settings.set {"changes":{"park_timeout_s":0}}`, `settings.get {}`}},
		{[]string{"idle-timeout", "300", "--agent", "b.two"}, "b.two: never closes while idle\n",
			[]string{`settings.set {"agent":"b.two","changes":{"park_timeout_s":300}}`, `settings.get {"agent":"b.two"}`}},
		{[]string{"idle-timeout", "300", "--agent", "b.two", "--clear"}, "b.two: never closes while idle\n",
			[]string{`settings.set {"agent":"b.two","changes":{"park_timeout_s":null}}`, `settings.get {"agent":"b.two"}`}},
		{[]string{"notice-digest", "2m", "--agent", "b.two"}, "b.two: results of work it opened are told in one digest 2m after " +
			"the first (sooner with any message); a failure, block or FATAL at once\n",
			[]string{`settings.set {"agent":"b.two","changes":{"notice_digest_s":120}}`, `settings.get {"agent":"b.two"}`}},
		{[]string{"notice-digest"}, "default: results of work it opened are told in one digest 15m after the first (sooner with " +
			"any message); a failure, block or FATAL at once\n", []string{`settings.get {}`}},
		{[]string{"ignore-cache-expiry", "--agent", "b.two", "--clear"}, "b.two: a resume may reuse its session however long it has been closed\n",
			[]string{`settings.set {"agent":"b.two","changes":{"ignore_cache_expiry":null}}`, `settings.get {"agent":"b.two"}`}},
		{[]string{"ignore-cache-expiry", "on"}, "default: a resume starts fresh once its prompt cache has expired (today's behaviour)\n",
			[]string{`settings.set {"changes":{"ignore_cache_expiry":true}}`, `settings.get {}`}},
		{[]string{"auto-accept", "off"}, "auto-accept: on - browser tool calls are said yes to once, and a declared folder's trust screen is accepted\n",
			[]string{`settings.set {"changes":{"auto_accept_dialogs":false}}`, `settings.get {}`}},
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
	if _, errs, code := r.run(nil, "idle-timeout", "--clear"); code != 1 || errs != "--clear only applies with --agent\n" {
		t.Errorf("clear: %q %d", errs, code)
	}
	if _, errs, code := r.run(nil, "auto-accept", "on"); code != 1 || errs != "error: config.yaml could not be written\n" {
		t.Errorf("refused: %q %d", errs, code)
	}
	if got := sent(calls()); !reflect.DeepEqual(got, []string{`settings.set {"changes":{"auto_accept_dialogs":true}}`}) {
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

func TestSettingsAnAgentOrAKeylessTerminalAndWhatIsNotParsedArePythons(t *testing.T) {
	keyless := agentsRig(t)
	keyless.daemon(nil)
	r := founderRig(t)
	calls := r.daemon(nil)
	cases := []struct {
		r    *rig
		env  []string
		args []string
	}{
		{keyless, nil, []string{"auto-accept"}},
		{r, asAgent, []string{"idle-timeout", "300"}},
		{r, nil, []string{"idle-timeout", "5m"}},
		{r, nil, []string{"idle-background", "--agent", "b.two"}},
		{r, nil, []string{"notice-digest", "0m"}},
		{r, nil, []string{"ignore-cache-expiry", "yes"}},
		{r, nil, []string{"auto-accept", "on", "--agent", "b.two"}},
	}
	for _, c := range cases {
		out, _, code := c.r.run(c.env, c.args...)
		if code != 0 || !strings.HasPrefix(out, "python ") || !strings.HasSuffix(out, " "+strings.Join(c.args, " ")+"\n") {
			t.Errorf("%v: %q %d", c.args, out, code)
		}
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("calls %v", got)
	}
}

func TestDevModeShowsAndSetsThroughSettings(t *testing.T) {
	r := founderRig(t)
	calls := r.daemon(map[string]string{"settings.get": `{"values":{"dev_mode":true}}`, "settings.set": `{"accepted":true,"said":"set"}`})
	if out, _, code := r.run(nil, "dev-mode"); code != 0 || out != "on\n" {
		t.Errorf("show: %q %d", out, code)
	}
	if out, _, code := r.run(nil, "dev-mode", "off"); code != 0 || out != "dev-mode: off\n" {
		t.Errorf("set: %q %d", out, code)
	}
	if got := sent(calls()); !reflect.DeepEqual(got, []string{`settings.get {}`, `settings.set {"changes":{"dev_mode":false}}`}) {
		t.Fatalf("calls %v", got)
	}
	if out, _, _ := r.run(asAgent, "dev-mode", "on", "--why", "w"); !strings.HasPrefix(out, "python ") {
		t.Errorf("an agent's proposal went to Go: %q", out)
	}
}
