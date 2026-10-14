package chatwrites

import (
	"bufio"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
)

func realDir(t *testing.T) string {
	t.Helper()
	d, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return d
}

// The founder's propose declares the root at once; list shows it with why and who; remove drops it, and a second remove finds nothing.
func TestTheFounderDeclaresListsAndRemovesARoot(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	root := realDir(t)
	add := `{"root":"` + root + `","request_id":"tp1"}`
	res := r.result(r.one(s, "trust.propose", add))
	if res["said"] != root+" is now a declared root" || res["proposal"] != nil {
		t.Fatal(res)
	}
	if res := r.result(r.one(s, "trust.propose", add)); res["replayed"] != true {
		t.Fatalf("a retry repeats nothing: %v", res)
	}
	r.refusedWith(r.one(s, "trust.propose", `{"root":"`+filepath.Join(root, "sub")+`","request_id":"tp2"}`), "")
	got := r.result(r.one(s, "trust.list", `{}`))
	declared, _ := got["declared"].([]any)
	if roots, _ := got["roots"].([]any); len(roots) != 1 || roots[0] != root || len(declared) != 1 {
		t.Fatal(got)
	}
	if d := declared[0].(map[string]any); d["why"] != "added by the founder" || d["added_by"] != "human" {
		t.Fatal(d)
	}
	if res := r.result(r.one(s, "trust.remove", `{"root":"`+root+`","request_id":"tr1"}`)); res["said"] != root+" is no longer a declared root" {
		t.Fatal(res)
	}
	if d := r.errData(r.one(s, "trust.remove", `{"root":"`+root+`","request_id":"tr2"}`)); d["code"] != "not_found" || d["name"] != root {
		t.Fatal(d)
	}
	if r.count("SELECT COUNT(*) FROM cursors WHERE name LIKE 'intent:%'") != 0 {
		t.Fatal("no intent is left behind")
	}
}

// An agent's propose raises a decision task for the founder and saves the proposal it waits on; nothing is declared yet.
func TestAnAgentsProposeRaisesADecisionForTheFounder(t *testing.T) {
	r := newRig(t)
	s := r.server("alice")
	root := realDir(t)
	r.refusedWith(r.one(s, "trust.propose", `{"root":"`+root+`","request_id":"tp0"}`), "")
	res := r.result(r.one(s, "trust.propose", `{"root":"`+root+`","why":" the repo ","request_id":"tp1"}`))
	id, _ := res["proposal"].(string)
	if id == "" {
		t.Fatal(res)
	}
	var tid, rid int64
	var owner, opener, expects string
	if err := r.db.QueryRow("SELECT id, owner, opened_by, expects FROM tasks").Scan(&tid, &owner, &opener, &expects); err != nil {
		t.Fatal(err)
	}
	if owner != "human" || opener != "alice" || expects != "decision" {
		t.Fatal(owner, opener, expects)
	}
	if err := r.db.QueryRow("SELECT request_id FROM task_requests WHERE task_id = ?", tid).Scan(&rid); err != nil {
		t.Fatal(err)
	}
	want := "proposal " + id + " raised as #" + strconv.FormatInt(rid, 10) + ". Nothing changes until the founder answers it; an approve is applied then, and you are told how it went."
	if res["said"] != want {
		t.Fatalf("said %q", res["said"])
	}
	var body string
	r.db.QueryRow("SELECT body FROM requests WHERE id = ?", rid).Scan(&body)
	if read, ok := team.RootRead(body, "alice"); !ok || read != root {
		t.Fatalf("the founder reads the root: %q", body)
	}
	p, ok, err := team.NewTrust(r.home).LoadProposal(id)
	if err != nil || !ok {
		t.Fatal(ok, err)
	}
	for k, v := range map[string]any{"root": root, "why": "the repo", "requested_by": "alice", "state": "proposed", "request": rid, "task": tid} {
		if p.Get(k) != v {
			t.Fatalf("%s = %v (%T), want %v", k, p.Get(k), p.Get(k), v)
		}
	}
	got := r.result(r.one(s, "trust.list", `{"proposals":true}`))
	if roots, _ := got["roots"].([]any); len(roots) != 0 {
		t.Fatal("nothing is declared before the founder answers", got)
	}
	props, _ := got["proposals"].([]any)
	if len(props) != 1 {
		t.Fatal(got)
	}
	if row := props[0].(map[string]any); row["id"] != id || row["root"] != root || row["state"] != "proposed" || row["requested_by"] != "alice" || row["request"] != float64(rid) {
		t.Fatal(row)
	}
	if got := r.result(r.one(s, "trust.list", `{}`)); len(got["proposals"].([]any)) != 0 {
		t.Fatal("proposals only when asked", got)
	}
}

func TestAnAgentProposesNoRootAlreadyDeclared(t *testing.T) {
	r := newRig(t)
	root := realDir(t)
	if ok, said := team.NewTrust(r.home).Enact(root, "w"); !ok {
		t.Fatal(said)
	}
	r.refusedWith(r.one(r.server("alice"), "trust.propose", `{"root":"`+filepath.Join(root, "wt")+`","why":"w","request_id":"tp1"}`), "")
	if r.count("SELECT COUNT(*) FROM tasks") != 0 {
		t.Fatal("no decision is raised")
	}
	if entries, _ := os.ReadDir(filepath.Join(r.home, "trust_proposals")); len(entries) != 0 {
		t.Fatal("no proposal is saved")
	}
}

// Removing a root is the founder's: an agent is refused, gate role, and the root stays.
func TestTrustRemoveIsRefusedToAnAgent(t *testing.T) {
	r := newRig(t)
	root := realDir(t)
	team.NewTrust(r.home).Enact(root, "w")
	r.refusedWith(r.one(r.server("alice"), "trust.remove", `{"root":"`+root+`","request_id":"tr1"}`), "role")
	if !team.NewTrust(r.home).IsDeclared(root) {
		t.Fatal("the root stays")
	}
}

func TestTrustParamsAreChecked(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	for _, c := range []struct{ method, params, field string }{
		{"trust.propose", `{"root":5,"request_id":"a"}`, "root"},
		{"trust.propose", `{"root":"rel","request_id":"b"}`, "root"},
		{"trust.propose", `{"root":"~/x","request_id":"c"}`, "root"},
		{"trust.propose", `{"root":"/r","why":5,"request_id":"d"}`, "why"},
		{"trust.list", `{"proposals":"yes"}`, "proposals"},
		{"trust.remove", `{"root":"x","request_id":"e"}`, "root"},
	} {
		if d := r.errData(r.one(s, c.method, c.params)); d["code"] != "bad_request" || d["field"] != c.field {
			t.Fatal(c, d)
		}
	}
}

// A paired device neither proposes nor declares a root: refused, gate role, with nothing raised or saved.
func TestADevicesTrustProposeIsRefused(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	cl, sv := net.Pipe()
	done := make(chan struct{})
	go func() { s.ServeRelayed(rpc.NewLineTransport(sv), "d1", "phone"); sv.Close(); close(done) }()
	defer func() { cl.Close(); <-done }()
	rd := bufio.NewReader(cl)
	cl.SetDeadline(time.Now().Add(10 * time.Second))
	var m map[string]any
	for _, line := range []string{`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1}}`,
		`{"jsonrpc":"2.0","id":2,"method":"trust.propose","params":{"root":"` + realDir(t) + `","why":"w","request_id":"tp1"}}`} {
		cl.Write([]byte(line + "\n"))
		b, err := rd.ReadBytes('\n')
		if err != nil {
			t.Fatal(err)
		}
		m = nil
		json.Unmarshal(b, &m)
	}
	r.refusedWith(m, "role")
	if r.count("SELECT COUNT(*) FROM tasks") != 0 {
		t.Fatal("no decision is raised")
	}
	if entries, _ := os.ReadDir(filepath.Join(r.home, "trust_proposals")); len(entries) != 0 {
		t.Fatal("no proposal is saved")
	}
}
