package main

import (
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestWithdrawReadAndInboxAnswerWithoutPython(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{
		"request.withdraw": `{"accepted":true,"said":"M7 withdrawn"}`,
		"request.read": `{"id":7,"requester":"a.one","target":"b.two","kind":"say","state":"landed","created":"2026-10-16T00:00:00Z","parent":null,
			"relation":"peer","response":"fire_and_forget","chain_depth":0,"body":"hello","answer":"fine","dismissal":null,"parts":[]}`,
		"inbox.queue": `{"newest":9,"head":{"id":9,"requester":"a.one","chain_depth":1,"body":"do it","more_waiting_line":"","how_to_close":"answer with hl say"},"history":[]}`,
	})
	if out, _, code := r.run(asAgent, "withdraw", "7", "--reason", "oops"); code != 0 || out != "M7 withdrawn\n" {
		t.Errorf("withdraw: %d %q", code, out)
	}
	out, _, code := r.run(asAgent, "read", "7")
	if code != 0 || !strings.Contains(out, "M7  a.one → b.two  say  landed") || !strings.Contains(out, "hello") ||
		!strings.Contains(out, "--- answered by b.two ---\nfine") {
		t.Errorf("read: %d %q", code, out)
	}
	out, _, code = r.run(asAgent, "inbox")
	if code != 0 || !strings.Contains(out, "M9 from a.one  depth=1\n\ndo it\n") {
		t.Errorf("inbox: %d %q", code, out)
	}
	want := []string{`request.withdraw {"id":7,"reason":"oops"}`, `request.read {"id":7,"wait_s":0}`,
		`inbox.queue {"all":false,"whose":"a.one"}`, `inbox.mark_read {"ids":[9]}`}
	if got := sent(calls()); !reflect.DeepEqual(got, want) {
		t.Errorf("calls\n got  %v\n want %v", got, want)
	}
}

func TestHrProposesThroughTheDaemonAndTakesOneBack(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{"hr.propose": `{"accepted":true,"proposal":3,"said":"proposal 00000003 raised."}`,
		"hr.withdraw": `{"accepted":true,"said":"proposal 0000000a withdrawn"}`})
	file := r.home + "/role.md"
	if err := os.WriteFile(file, []byte("the role"), 0o644); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{
		{"hr", "add", "c.three", "--why", "w", "--repo", "/r", "--effort", "high"},
		{"hr", "retire", "c.three", "--why", "w"},
		{"hr", "link", "a.one", "b.two", "--why", "w"},
		{"hr", "context", "b.two", "--role-file", file, "--why", "w"},
		{"hr", "withdraw", "a"},
	} {
		if out, errs, code := r.run(asAgent, args...); code != 0 || errs != "" || out == "" {
			t.Errorf("%v: %d %q %q", args, code, out, errs)
		}
	}
	want := []string{
		`hr.propose {"args":{"effort":"high","name":"c.three","repo":"/r"},"kind":"add","why":"w"}`,
		`hr.propose {"args":{"name":"c.three"},"kind":"retire","why":"w"}`,
		`hr.propose {"args":{"name":"a.one","to":"b.two"},"kind":"link","why":"w"}`,
		`hr.propose {"args":{"name":"b.two","sets":{"ROLE.md":"the role"}},"kind":"context","why":"w"}`,
		`hr.withdraw {"proposal":10}`,
	}
	if got := sent(calls()); !reflect.DeepEqual(got, want) {
		t.Errorf("calls\n got  %v\n want %v", got, want)
	}
}

func TestReadWithdrawAndSayParentSendARefAsTextAndANumberAsANumber(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{
		"request.withdraw": `{"accepted":true,"said":"withdrawn"}`,
		"request.read": `{"id":7,"requester":"a.one","target":"b.two","kind":"say","state":"landed","created":"2026-10-16T00:00:00Z","parent":null,
			"relation":"peer","response":"fire_and_forget","chain_depth":0,"body":"hello","answer":null,"dismissal":null,"parts":[]}`,
		"say": `{"accepted":true,"said":"sent"}`,
	})
	r.run(asAgent, "read", "C0FFEE")
	r.run(asAgent, "read", "M7")
	r.run(asAgent, "withdraw", "123456789012")
	r.run(asAgent, "say", "b.two", "hi", "--parent", "c0ffee000001", "--no-task", "chat")
	want := []string{`request.read {"id":"c0ffee","wait_s":0}`, `request.read {"id":7,"wait_s":0}`, `request.withdraw {"id":"123456789012"}`}
	got := sent(calls())
	if len(got) < 4 || !reflect.DeepEqual(got[:3], want) || !strings.Contains(got[3], `"parent":"c0ffee000001"`) {
		t.Errorf("calls\n got  %v\n want %v then a say with the parent ref", got, want)
	}
	// #<n> is a PR: each verb that names a message refuses it with one line and calls nothing
	for _, args := range [][]string{{"read", "#42"}, {"withdraw", "#42"}, {"say", "b.two", "hi", "--parent", "#42", "--no-task", "chat"},
		{"task", "done", "T1", "--request", "#42"}} {
		if out, _, code := r.run(asAgent, args...); code != 1 || out != "#42 is PR 42; a message is M42\n" {
			t.Errorf("%v: %d %q", args, code, out)
		}
	}
	if got := sent(calls()); len(got) != 4 {
		t.Errorf("a refused #<n> calls nothing: %v", got)
	}
	for _, bad := range []string{"c0ffe", "nothex", "1234567890123"} {
		if _, errs, code := r.run(asAgent, "read", bad); code != 2 || !strings.Contains(errs, "arguments not understood") {
			t.Errorf("%s: %d %q", bad, code, errs)
		}
	}
}

func TestAPortedVerbRefusesWhatItDoesNotParseInsteadOfRunningPython(t *testing.T) {
	r := agentsRig(t)
	r.daemon(nil)
	for _, args := range [][]string{{"say", "--bogus"}, {"task"}, {"task", "new", "--bogus"}, {"read"}, {"hr", "approve", "x"}, {"team", "extra"}} {
		out, errs, code := r.run(asAgent, args...)
		if code != 2 || strings.Contains(out+errs, "python") || !strings.Contains(errs, "arguments not understood") {
			t.Errorf("%v: %d %q %q", args, code, out, errs)
		}
	}
}

func TestHrRenameTeamAndDiffGoThroughTheDaemon(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{"hr.propose": `{"accepted":true,"proposal":3,"said":"proposal 00000003 raised."}`})
	if err := os.WriteFile(r.home+"/role.md", []byte("the role"), 0o644); err != nil {
		t.Fatal(err)
	}
	diff := "changes:\n  - kind: rename\n    name: a.one\n    to: a.uno\n  - kind: context\n    name: b.two\n    role_file: " + r.home + "/role.md\n"
	if err := os.WriteFile(r.home+"/diff.yaml", []byte(diff), 0o644); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{
		{"hr", "rename", "a.one", "--to", "a.uno", "--why", "w"},
		{"hr", "team", "zed", "--member", "sea=/s", "--member", "air", "--repo", "/r", "--why", "w"},
		{"hr", "diff", "--file", r.home + "/diff.yaml", "--why", "w"},
	} {
		if out, errs, code := r.run(asAgent, args...); code != 0 || errs != "" || out == "" {
			t.Errorf("%v: %d %q %q", args, code, out, errs)
		}
	}
	want := []string{
		`hr.propose {"args":{"name":"a.one","to":"a.uno"},"kind":"rename","why":"w"}`,
		`hr.propose {"args":{"members":["sea","air"],"name":"zed","repo":"/r","repos":{"zed":"/r","zed.sea":"/s"}},"kind":"team","why":"w"}`,
		`hr.propose {"args":{"changes":[{"kind":"rename","name":"a.one","to":"a.uno"},{"kind":"context","name":"b.two","sets":{"ROLE.md":"the role"}}],"name":"2 changes"},"kind":"bundle","why":"w"}`,
	}
	if got := sent(calls()); !reflect.DeepEqual(got, want) {
		t.Errorf("calls\n got  %v\n want %v", got, want)
	}
	for _, args := range [][]string{{"hr", "rename", "a.one", "--why", "w"}, {"hr", "team", "zed", "--why", "w"}} {
		if _, errs, code := r.run(asAgent, args...); code != 2 || !strings.Contains(errs, "arguments not understood") {
			t.Errorf("%v: %d %q", args, code, errs)
		}
	}
}
