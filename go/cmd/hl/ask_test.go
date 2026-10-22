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
		"request.withdraw": `{"accepted":true,"said":"#7 withdrawn"}`,
		"request.read": `{"id":7,"name":"msg_0102030","requester":"a.one","target":"b.two","kind":"say","state":"landed","created":"2026-10-16T00:00:00Z","parent":null,
			"chain_depth":0,"body":"hello","answer":"fine","parts":[]}`,
		"inbox.queue": `{"newest":9,"head":{"id":9,"name":"msg_0405060","requester":"a.one","chain_depth":1,"body":"do it","more_waiting_line":"","how_to_close":"answer with hl say"},"history":[]}`,
	})
	if out, _, code := r.run(asAgent, "withdraw", "7", "--reason", "oops"); code != 0 || out != "#7 withdrawn\n" {
		t.Errorf("withdraw: %d %q", code, out)
	}
	out, _, code := r.run(asAgent, "read", "7")
	if code != 0 || !strings.Contains(out, "msg_0102030  a.one → b.two  say  landed") || !strings.Contains(out, "hello") ||
		!strings.Contains(out, "--- reply from b.two ---\nfine") {
		t.Errorf("read: %d %q", code, out)
	}
	out, _, code = r.run(asAgent, "inbox")
	if code != 0 || !strings.Contains(out, "msg_0405060 from a.one  depth=1\n\ndo it\n") {
		t.Errorf("inbox: %d %q", code, out)
	}
	want := []string{`request.withdraw {"id":7,"reason":"oops"}`, `request.read {"id":7,"wait_s":0}`,
		`inbox.queue {"all":false,"whose":"a.one"}`, `inbox.mark_read {"ids":[9]}`}
	if got := sent(calls()); !reflect.DeepEqual(got, want) {
		t.Errorf("calls\n got  %v\n want %v", got, want)
	}
}

func TestReadTakesAMessageByNameOrHashAndPassesAHashMarkOnToBeRefused(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{"request.read": "error:#7 is PR 7; a message is named msg_<hash>"})
	// a hash may be all digits: only digits shorter than a printed name are an id
	for _, arg := range []string{"MSG_0102030", "0a1b2c3", "3412789", "39568", "#7"} {
		r.run(asAgent, "read", arg)
	}
	want := []string{`request.read {"id":"0102030","wait_s":0}`, `request.read {"id":"0a1b2c3","wait_s":0}`,
		`request.read {"id":"3412789","wait_s":0}`, `request.read {"id":39568,"wait_s":0}`, `request.read {"id":"#7","wait_s":0}`}
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

func TestHrBundleSendsTheChangesAsOneProposalAndAWhyWithAMessageNumberIsRefused(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{"hr.propose": `{"accepted":true,"proposal":3,"said":"proposal 00000003 raised."}`})
	file := r.home + "/changes.json"
	if err := os.WriteFile(file, []byte(`[{"kind":"retire","name":"a.one"},{"kind":"rename","name":"b.two","to":"b.dos"}]`), 0o644); err != nil {
		t.Fatal(err)
	}
	if out, errs, code := r.run(asAgent, "hr", "bundle", "--file", file, "--why", "tidy the team"); code != 0 || errs != "" || out == "" {
		t.Errorf("bundle: %d %q %q", code, out, errs)
	}
	if _, errs, _ := r.run(asAgent, "hr", "bundle", "--file", file, "--why", "as asked in msg_8aebff7"); !strings.Contains(errs, "names msg_8aebff7") {
		t.Errorf("a message number in --why: %q", errs)
	}
	for _, args := range [][]string{
		{"hr", "bundle", "--file", file, "--why", "as asked in msg_8aebff7"},
		{"hr", "retire", "a.one", "--why", "per ocp-msg 1a2b3c4d"},
		{"hr", "bundle", "--why", "w"},
		{"hr", "bundle", "--file", file},
	} {
		if _, errs, code := r.run(asAgent, args...); code != 2 || !strings.Contains(errs, "arguments not understood") {
			t.Errorf("%v: %d %q", args, code, errs)
		}
	}
	want := []string{`hr.propose {"args":{"changes":[{"kind":"retire","name":"a.one"},{"kind":"rename","name":"b.two","to":"b.dos"}],"name":"2 changes"},"kind":"bundle","why":"tidy the team"}`}
	if got := sent(calls()); !reflect.DeepEqual(got, want) {
		t.Errorf("calls\n got  %v\n want %v", got, want)
	}
}

func TestReadWithdrawAndSayParentSendARefAsTextAndANumberAsANumber(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{
		"request.withdraw": `{"accepted":true,"said":"withdrawn"}`,
		"request.read": `{"id":7,"name":"msg_0102030","requester":"a.one","target":"b.two","kind":"say","state":"landed","created":"2026-10-16T00:00:00Z","parent":null,
			"relation":"peer","response":"fire_and_forget","chain_depth":0,"body":"hello","answer":null,"dismissal":null,"parts":[]}`,
		"say": `{"accepted":true,"said":"sent"}`,
	})
	r.run(asAgent, "read", "MSG_0100200")
	r.run(asAgent, "read", "7")
	r.run(asAgent, "withdraw", "123456789012")
	r.run(asAgent, "say", "b.two", "hi", "--parent", "c0ffee000001", "--no-task", "chat")
	want := []string{`request.read {"id":"0100200","wait_s":0}`, `request.read {"id":7,"wait_s":0}`, `request.withdraw {"id":"123456789012"}`}
	got := sent(calls())
	if len(got) < 4 || !reflect.DeepEqual(got[:3], want) || !strings.Contains(got[3], `"parent":"c0ffee000001"`) {
		t.Errorf("calls\n got  %v\n want %v then a say with the parent ref", got, want)
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
	for _, args := range [][]string{{"say", "--bogus"}, {"task"}, {"task", "new", "--bogus"}, {"read"}, {"hr", "approve"}, {"team", "extra"}} {
		out, errs, code := r.run(asAgent, args...)
		if code != 2 || strings.Contains(out+errs, "python") || !strings.Contains(errs, "arguments not understood") {
			t.Errorf("%v: %d %q %q", args, code, out, errs)
		}
	}
}

func TestHrRenameAndTeamGoThroughTheDaemon(t *testing.T) {
	r := agentsRig(t)
	calls := r.daemon(map[string]string{"hr.propose": `{"accepted":true,"proposal":3,"said":"proposal 00000003 raised."}`})
	for _, args := range [][]string{
		{"hr", "rename", "a.one", "--to", "a.uno", "--why", "w"},
		{"hr", "team", "zed", "--member", "sea=/s", "--member", "air", "--repo", "/r", "--why", "w"},
	} {
		if out, errs, code := r.run(asAgent, args...); code != 0 || errs != "" || out == "" {
			t.Errorf("%v: %d %q %q", args, code, out, errs)
		}
	}
	want := []string{
		`hr.propose {"args":{"name":"a.one","to":"a.uno"},"kind":"rename","why":"w"}`,
		`hr.propose {"args":{"members":["sea","air"],"name":"zed","repo":"/r","repos":{"zed":"/r","zed.sea":"/s"}},"kind":"team","why":"w"}`,
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
