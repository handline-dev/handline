package main

import (
	"reflect"
	"strings"
	"testing"
)

func TestSaySendsItsParamsAndPrintsWhatTheDaemonSaid(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{"say": `{"accepted":true,"said":"queued as #9 for b.two"}`})
	cases := []struct {
		stdin string
		args  []string
		want  string
	}{
		{"", []string{"say", "b.two", "hi there"}, `say {"body":"hi there","target":"b.two"}`},
		{"", []string{"say", "human", "done", "--parent", "7", "--task", "T3"}, `say {"body":"done","parent":7,"target":"human","task":3}`},
		{"", []string{"say", "human", "ok", "--parent=7", "--no-task", "nothing to build"},
			`say {"body":"ok","no_task":"nothing to build","parent":7,"target":"human"}`},
		{"from stdin", []string{"say", "b.two", "-"}, `say {"body":"from stdin","target":"b.two"}`},
		{"piped", []string{"say", "b.two"}, `say {"body":"piped","target":"b.two"}`},
		{"heredoc\n", []string{"say", "b.two", "--file", "-"}, `say {"body":"heredoc\n","target":"b.two"}`},
		{"", []string{"say", "human", "look", "--attach", "/tmp/a.png", "--attach", "/tmp/b.png"},
			`say {"body":"look","files":["/tmp/a.png","/tmp/b.png"],"target":"human"}`},
	}
	seen := 0
	for _, c := range cases {
		out, errs, code := r.runIn(c.stdin, asAgent, c.args...)
		all := sent(calls())
		got := all[seen:]
		seen = len(all)
		if code != 0 || errs != "" || out != "queued as #9 for b.two\n" || !reflect.DeepEqual(got, []string{c.want}) {
			t.Errorf("%v: exit %d, out %q, err %q\n got  %v\n want %v", c.args, code, out, errs, got, c.want)
		}
	}
}

func TestSayRefusedSaysWhyAndFails(t *testing.T) {
	r := agentsRig(t)
	r.daemon(map[string]string{"say": "error:no agent named z.z"})
	if out, _, code := r.run(asAgent, "say", "z.z", "hi"); code != 1 || out != "no agent named z.z\n" {
		t.Errorf("%q %d", out, code)
	}
}

func TestSayWarnsOfAGapAShellLeft(t *testing.T) {
	r := agentsRig(t)
	r.daemon(map[string]string{"say": `{"accepted":true,"said":"sent"}`})
	warn := "warning: the body has a gap"
	for body, want := range map[string]bool{"ran  then": true, "naïve  café": true, "a_  _b": true, "end.  Next": false,
		"one space": false, "trailing  ": false} {
		out, _, _ := r.run(asAgent, "say", "b.two", body)
		if strings.HasPrefix(out, warn) != want {
			t.Errorf("%q: %q", body, out)
		}
	}
	if out, _, _ := r.runIn("ran  then", asAgent, "say", "b.two", "--file", "-"); strings.HasPrefix(out, warn) {
		t.Errorf("--file warned: %q", out)
	}
}

func TestSayRefusalsBeforeAnyCall(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(nil)
	cases := []struct {
		env  []string
		args []string
		want string
	}{
		{asAgent, []string{"say"}, "say needs a target and a body\n" + sayUsage + "\n"},
		{asAgent, []string{"say", "b.two"}, "body is required (inline, --file PATH, or on stdin)\n"},
		{asAgent, []string{"say", "b.two", "x", "--file", "f"}, "give body inline or with --file, not both\n"},
		{nil, []string{"say", "b.two", "x"}, "cannot tell who is saying this: OCP_AGENT is unset and this is not an " +
			"interactive shell. Agents are started with `hl launch <agent>`, which sets it.\n"},
	}
	for _, c := range cases {
		if out, _, code := r.run(c.env, c.args...); code != 1 || out != c.want {
			t.Errorf("%v: exit %d\n got  %q\n want %q", c.args, code, out, c.want)
		}
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("calls %v", got)
	}
}

func TestWhatTheSayPortDoesNotParseIsRefused(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(nil)
	for _, args := range [][]string{
		{"say", "a", "b", "c"}, {"say", "a", "b", "--parent", "x"}, {"say", "a", "b", "--task", "3"}, {"say", "a", " "},
		{"say", "", "b"}, {"say", "a", "b", "--no-task", ""}, {"say", "a", "--", "b"},
	} {
		out, errs, code := r.run(asAgent, args...)
		if code != 2 || out != "" || !strings.Contains(errs, "arguments not understood") {
			t.Errorf("%v: %q %q %d", args, out, errs, code)
		}
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("calls %v", got)
	}
}

func TestSayHelpShowsTheHeredoc(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(nil)
	for _, args := range [][]string{{"say", "--help"}, {"say", "a", "b", "-h"}} {
		if out, _, code := r.run(asAgent, args...); code != 0 || out != sayUsage+"\n" || !strings.Contains(out, "--file - <<'EOF'") {
			t.Errorf("%v: %q %d", args, out, code)
		}
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("calls %v", got)
	}
}

func TestALongOneLineBodyGetsAHintAndStillGoes(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{"say": `{"accepted":true,"said":"sent"}`})
	long := strings.Repeat("word ", 61)
	hint := "hint: this body is 305 characters on one line. The receiver sees its lines: next time put each point on " +
		"its own line, with `hl say b.two --file - <<'EOF'`.\n"
	for _, c := range []struct {
		stdin string
		args  []string
		want  string
	}{
		{"", []string{"say", "b.two", long}, hint + "sent\n"},
		{long, []string{"say", "b.two", "--file", "-"}, hint + "sent\n"},
		{long[:300], []string{"say", "b.two", "--file", "-"}, "sent\n"},
		{long + "\n" + long, []string{"say", "b.two", "--file", "-"}, "sent\n"},
	} {
		if out, _, code := r.runIn(c.stdin, asAgent, c.args...); code != 0 || out != c.want {
			t.Errorf("%v: %q %d", c.args[2:], out, code)
		}
	}
	if got := calls(); len(got) != 4 {
		t.Fatalf("calls %v", got)
	}
}

func TestSayToHumanThatAsksIsRefusedToATask(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{"say": `{"accepted":true,"said":"sent"}`})
	for body, refused := range map[string]bool{"답장 한 번 주세요": true, "ok to merge?": true, "please reply": true,
		"merged and deployed": false, "deployed, confirmed working": false, "he replied already": false, "which one? picked A": false} {
		out, _, code := r.run(asAgent, "say", "human", body)
		if refused != (code == 1 && strings.Contains(out, "hl task new --assign human")) {
			t.Errorf("%q: exit %d, %q", body, code, out)
		}
	}
	if n := len(sent(calls())); n != 4 {
		t.Errorf("only the two plain says reach the daemon: %d", n)
	}
	if out, _, code := r.run(asAgent, "say", "b.two", "ok to merge?"); code != 0 {
		t.Errorf("a question to an agent is fine: %q", out)
	}
}
