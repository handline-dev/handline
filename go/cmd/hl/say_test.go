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
		{asAgent, []string{"say"}, "say needs a target and a body\n"},
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

func TestWhatTheSayPortDoesNotParseIsPythons(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(nil)
	for _, args := range [][]string{
		{"say", "a", "b", "c"}, {"say", "a", "b", "--parent", "x"}, {"say", "a", "b", "--task", "3"}, {"say", "a", " "},
		{"say", "", "b"}, {"say", "a", "b", "--no-task", ""}, {"say", "a", "b", "-h"}, {"say", "a", "--", "b"},
	} {
		out, _, code := r.run(asAgent, args...)
		if code != 0 || !strings.HasPrefix(out, "python ") || !strings.HasSuffix(out, " "+strings.Join(args, " ")+"\n") {
			t.Errorf("%v: %q %d", args, out, code)
		}
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("calls %v", got)
	}
}
