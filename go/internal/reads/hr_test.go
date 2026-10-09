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
	// a retire asks for no sets, whatever else the file holds
	if want := map[string]any{"vendor": "codex"}; !reflect.DeepEqual(p["asked"], want) {
		t.Fatalf("%v", p["asked"])
	}
	if len(p["subject"].(string)) != 64 {
		t.Fatalf("%v", p["subject"])
	}
}

// A proposal's diff is what approving does, in words, one change per line (the spec's Proposal.diff), as rpc_hr._change_lines words it.
func TestProposalDiffIsOneChangePerLineInTheSpecsWords(t *testing.T) {
	cases := []struct{ name, file, want string }{
		{"retire", "kind: retire\nagent: old\n", "retire old"},
		{"add", "kind: add\nagent: new\nteam: eng\nmodel: opus\nvendor: codex\n", "register new, team eng, model opus, vendor codex"},
		{"settings", "kind: settings\nagent: w\nset.model: opus\nset.effort: \"\"\n", "set model of w to opus\nset effort of w to (none)"},
		{"settings with nothing set", "kind: settings\nagent: w\n", "change the settings of w"},
		{"link", "kind: link\nagent: a\nto: b\nrelation: peer\n", "allow a to ask b (response async, relation peer)"},
		{"unlink", "kind: unlink\nagent: a\nto: b\n", "remove the edge a -> b"},
		{"team", "kind: team\nagent: eng\nrepo: r\nmembers: \"a,b\"\n", "create team eng (working in r) with members a, b"},
		{"context", "kind: context\nagent: w\nset.ROLE.md: \"\\\"x\\\"\"\n", "replace ROLE.md of w"},
		{"rename", "kind: rename\nagent: a\nto: b\n", "rename a to b"},
		{"schedule", "kind: schedule\nagent: old\nset.action: add\n", "schedule old"},
		{"global", "kind: global\nagent: dev-mode\nset.value: \"on\"\n", "global dev-mode"},
		{"bundle, in the order it is applied", "kind: bundle\nagent: x\ni1.kind: retire\ni1.agent: old\ni2.kind: add\ni2.agent: new\ni3.kind: rename\ni3.agent: a\ni3.to: b\n",
			"register new\nrename a to b\nretire old"},
	}
	for _, c := range cases {
		f, err := parseFlat("id: \"0000000a\"\nrequested_by: boss\ncreated: 1\nstate: proposed\n" + c.file)
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		if got := f.diff(); got != c.want {
			t.Errorf("%s: %q, want %q", c.name, got, c.want)
		}
	}
}

func TestAskedSetsAreOnlyForSettingsAndABundleNamesItsChanges(t *testing.T) {
	e := testEnv(t)
	putProposal(t, e, "0000000a", "id: \"0000000a\"\nkind: retire\nagent: old\nrequested_by: boss\ncreated: 1\nstate: proposed\nset.model: opus\n")
	putProposal(t, e, "0000000b", "id: \"0000000b\"\nkind: settings\nagent: w\nrequested_by: boss\ncreated: 1\nstate: proposed\nset.model: opus\n")
	putProposal(t, e, "0000000c", "id: \"0000000c\"\nkind: bundle\nagent: x\nrequested_by: boss\ncreated: 1\nstate: proposed\ni1.kind: retire\ni1.agent: old\ni2.kind: add\ni2.agent: new\n")
	got := map[int64]map[string]any{}
	for _, v := range e.HRProposals() {
		got[v["id"].(int64)] = v
	}
	if _, has := got[10]["asked"]; has {
		t.Errorf("a retire asks for no sets: %v", got[10]["asked"])
	}
	if a, _ := got[11]["asked"].(map[string]any); a["sets"] == nil {
		t.Errorf("a settings proposal asks for its sets: %v", got[11]["asked"])
	}
	if got[12]["agent"] != "2 changes" || got[12]["title"] != "bundle 2 changes" {
		t.Errorf("a bundle is named by its changes: %v %v", got[12]["agent"], got[12]["title"])
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

func TestHrListServesAScheduleAndAGlobalProposalWithTheirWords(t *testing.T) {
	e := testEnv(t)
	putProposal(t, e, "0000000d", "id: \"0000000d\"\nkind: schedule\nagent: old\nrequested_by: boss\nwhy: w\ncreated: 5\nstate: proposed\nset.action: add\nset.cron: \"30 9 * * 1-5\"\nset.message: morning\n")
	putProposal(t, e, "0000000e", "id: \"0000000e\"\nkind: global\nagent: dev-mode\nrequested_by: boss\nwhy: w\ncreated: 6\nstate: proposed\nset.value: \"on\"\n")
	got, err := e.hrList(nil, map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	list := got.(map[string]any)["proposals"].([]any)
	if len(list) != 2 {
		t.Fatalf("both are listed: %v", list)
	}
	if d := list[0].(map[string]any)["diff"]; d != "schedule old" {
		t.Fatalf("%q", d)
	}
	if d := list[1].(map[string]any)["diff"].(string); d != "global dev-mode" {
		t.Fatalf("%q", d)
	}
}
