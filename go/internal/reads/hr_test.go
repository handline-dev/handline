package reads

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func putProposal(t *testing.T, e *Env, id, body string) {
	t.Helper()
	dir := filepath.Join(e.Home, "hr")
	os.MkdirAll(dir, 0o755)
	if err := os.WriteFile(filepath.Join(dir, id+".yaml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func proposalFile(id, state string, created, at int64, extra string) string {
	s := fmt.Sprintf("id: \"%s\"\nkind: retire\nagent: old\nrequested_by: boss\nwhy: \"it is idle: \\\"really\\\"\"\ncreated: %d\nstate: %s\n", id, created, state)
	if at != 0 {
		s += fmt.Sprintf("applied: %d\n", at)
	}
	return s + extra
}

func hrIDs(t *testing.T, e *Env, params map[string]any) []int64 {
	t.Helper()
	got, err := e.hrList(nil, params)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(got)
	var out struct{ Proposals []map[string]any }
	json.Unmarshal(b, &out)
	ids := []int64{}
	for _, p := range out.Proposals {
		ids = append(ids, int64(p["id"].(float64)))
	}
	return ids
}

func TestHrListServesWaitingOldestFirstThenSettledNewestFirstWithinAWeek(t *testing.T) {
	e := testEnv(t)
	now := time.Now().Unix()
	day := int64(86400)
	putProposal(t, e, "0000000b", proposalFile("0000000b", "proposed", now-10, 0, ""))
	putProposal(t, e, "0000000a", proposalFile("0000000a", "approved", now-50, now-5, "error: no room\n"))
	putProposal(t, e, "0000000c", proposalFile("0000000c", "applied", now-20*day, now-9*day, ""))
	putProposal(t, e, "0000000d", proposalFile("0000000d", "rejected", now-3*day, now-2*day, ""))
	putProposal(t, e, "0000000e", proposalFile("0000000e", "withdrawn", now-3*day, now-day, ""))
	putProposal(t, e, "0000000f", proposalFile("0000000f", "enacting", now-3*day, now-day, ""))                        // not a state it serves
	putProposal(t, e, "00000010", proposalFile("00000010", "applied", now-3*day, now-day, "")+"requested_by: human\n") // the founder's own
	putProposal(t, e, "bad", "id: [\n")
	putProposal(t, e, "notone", "kind: nothing\n")
	if got, want := hrIDs(t, e, map[string]any{}), []int64{10, 11, 14, 13}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v want %v", got, want)
	}
	if got, want := hrIDs(t, e, map[string]any{"state": "approved"}), []int64{12}; !reflect.DeepEqual(got, want) {
		t.Fatalf("a state has no window: got %v want %v", got, want)
	}
	if got := hrIDs(t, e, map[string]any{"state": "no_such_state"}); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
	if _, err := e.hrList(nil, map[string]any{"state": json.Number("5")}); err == nil {
		t.Fatal("a state that is not text was served")
	}
}

func TestHrListSettledAreAtMostFifty(t *testing.T) {
	e := testEnv(t)
	now := time.Now().Unix()
	for i := 1; i <= 60; i++ {
		id := fmt.Sprintf("%08x", i)
		putProposal(t, e, id, proposalFile(id, "rejected", now-1000, now-int64(i), ""))
	}
	ids := hrIDs(t, e, map[string]any{})
	if len(ids) != 50 || ids[0] != 1 {
		t.Fatalf("%d, first %d", len(ids), ids[0])
	}
}

func TestHrListProposalFields(t *testing.T) {
	e := testEnv(t)
	putProposal(t, e, "000000aa", proposalFile("000000aa", "approved", 100, 200, "error: team.yaml is read-only\nvendor: codex\nset.model: opus\n"))
	got, _ := e.hrList(nil, map[string]any{})
	p := got.(map[string]any)["proposals"].([]any)[0].(map[string]any)
	if p["state"] != "apply_failed" || p["error"] != "team.yaml is read-only" || p["settled"] != "1970-01-01T00:03:20Z" ||
		p["reason"] != `it is idle: "really"` || p["requested_by"] != "boss" || p["title"] != "retire old" {
		t.Fatalf("%v", p)
	}
	if want := map[string]any{"vendor": "codex", "sets": map[string]any{"model": "opus"}}; !reflect.DeepEqual(p["asked"], want) {
		t.Fatalf("%v", p["asked"])
	}
	if len(p["subject"].(string)) != 64 {
		t.Fatalf("%v", p["subject"])
	}
}

func TestContextProposalDiffIsUnified(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "w", "one\ntwo\nthree\n")
	f, err := parseFlat("id: 1\nkind: context\nagent: w\nset.ROLE.md: \"\\\"one\\nTWO\\nthree\\n\\\"\"\n")
	if err != nil {
		t.Fatal(err)
	}
	f.m["set.ROLE.md"] = `"one\nTWO\nthree\n"`
	want := "w's standing context would be replaced, as a diff of what is there now:\n--- ROLE.md (now)\n+++ ROLE.md (proposed)\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\nWritten from the next launch; a running session is not restarted."
	if got := e.diff(f); got != want {
		t.Fatalf("%q", got)
	}
}

func TestSubjectIsThePythonDigest(t *testing.T) {
	f, err := parseFlat("id: \"000000aa\"\nkind: settings\nagent: w\nrequested_by: boss\nwhy: \"caf\\\\u00e9 é\"\ncreated: 100\nstate: proposed\nset.model: opus\nset.effort: high\n")
	if err != nil {
		t.Fatal(err)
	}
	// hr.subject_of of the same file, from handline/team/hr.py
	if got, want := f.subject(), "e2f9f9ce53ee8e601f2721a3586eceb6a20f0057b0408a78cae763ebd5627c69"; got != want {
		t.Fatalf("%s", got)
	}
}

func TestHrListCutsAnErrorAt400Characters(t *testing.T) {
	e := testEnv(t)
	long := ""
	for i := 0; i < 500; i++ {
		long += "é"
	}
	putProposal(t, e, "000000bb", proposalFile("000000bb", "approved", 100, 200, "error: "+long+"\n"))
	got, _ := e.hrList(nil, map[string]any{})
	p := got.(map[string]any)["proposals"].([]any)[0].(map[string]any)
	if n := len([]rune(p["error"].(string))); n != 400 || p["kind"] != "retire" {
		t.Fatalf("%d %v", n, p["kind"])
	}
}
