package main

import (
	"reflect"
	"testing"
)

func TestCloseAndRestartSendTheirMethodAndPrintWhatTheDaemonSaid(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{"session.stop": `{"accepted":true,"said":"b.two: closed"}`,
		"session.restart": "error:b.two: mid-turn; --force restarts it once the turn ends"})
	cases := []struct {
		args []string
		want string
		out  string
		code int
	}{
		{[]string{"close", "b.two"}, `session.stop {"agent":"b.two","mode":"close"}`, "b.two: closed\n", 0},
		{[]string{"close", "--fresh", "b.two"}, `session.stop {"agent":"b.two","fresh":true,"mode":"close"}`, "b.two: closed\n", 0},
		{[]string{"restart", "b.two", "--fresh", "--force"}, `session.restart {"agent":"b.two","force":true,"fresh":true}`,
			"b.two: mid-turn; --force restarts it once the turn ends\n", 1},
		{[]string{"restart", "T5 "}, `session.restart {"agent":"T5 "}`, "b.two: mid-turn; --force restarts it once the turn ends\n", 1},
	}
	seen := 0
	for _, c := range cases {
		out, errs, code := r.run(asAgent, c.args...)
		all := sent(calls())
		got := all[seen:]
		seen = len(all)
		if code != c.code || errs != "" || out != c.out || !reflect.DeepEqual(got, []string{c.want}) {
			t.Errorf("%v: exit %d, out %q, err %q\n got  %v\n want %v", c.args, code, out, errs, got, c.want)
		}
	}
}

func TestCloseAndRestartRefuseATaskOrANumber(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(nil)
	cases := map[string][]string{
		"'T5' is not an agent. A task has no close: `hl task done T5` ends it, and `hl task reopen T5` undoes a wrong result.\n": {"close", "T5"},
		"'12' is not an agent. A task has no close: `hl task done 12` ends it, and `hl task reopen 12` undoes a wrong result.\n": {"close", "12"},
		"'t7' is not an agent.\n": {"restart", "t7"},
	}
	for want, args := range cases {
		if out, _, code := r.run(asAgent, args...); code != 1 || out != want {
			t.Errorf("%v: exit %d\n got  %q\n want %q", args, code, out, want)
		}
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("calls %v", got)
	}
}

func TestRestartAnswersWhatItDoesNotParseWithItsUsage(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(nil)
	const use = "usage: hl restart [--fresh] [--force] target\n"
	for _, c := range []struct {
		args      []string
		out, errs string
		code      int
	}{
		{[]string{"restart", "a", "-h"}, use, "", 0},
		{[]string{"restart", "--fre", "a"}, "", use, 2},
		{[]string{"restart"}, "", use, 2},
		{[]string{"restart", "a", "b"}, "", use, 2},
	} {
		out, errs, code := r.run(asAgent, c.args...)
		if out != c.out || errs != c.errs || code != c.code {
			t.Errorf("%v: out %q err %q exit %d", c.args, out, errs, code)
		}
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("calls %v", got)
	}
}

func TestWhatTheSessionPortDoesNotParseIsRefused(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(nil)
	for _, args := range [][]string{
		{"close"}, {"close", "a", "b"}, {"close", "a", "--force"}, {"close", "١٢"},
	} {
		out, _, code := r.run(asAgent, args...)
		if code != 2 || out != "" {
			t.Errorf("%v: %q %d", args, out, code)
		}
	}
	if got := calls(); len(got) != 0 {
		t.Fatalf("calls %v", got)
	}
}
