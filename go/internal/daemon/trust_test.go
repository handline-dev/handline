package daemon

import (
	"database/sql"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/team"
)

// trustDaemon is a wired daemon whose resolve applies trust proposals, with alice's proposal for a fresh root raised as the founder's
// decision: its id, card and root.
func trustDaemon(t *testing.T) (d *Daemon, id string, card int64, root string) {
	t.Helper()
	for _, k := range []string{"OCP_AGENT", "HANDLINE_AGENT"} { // the protected store refuses an agent's session
		t.Setenv(k, "")
	}
	d, _ = wiredDaemon(t, true, func(q *sql.DB, home string) supervisor.Proposals {
		return team.TrustProposals{Trust: team.NewTrust(home), Q: q}
	})
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	raise := func(title, text string) (bool, string, int64, int64, error) {
		rid, err := db.AddRequest(d.db, db.NewRequest{Requester: "alice", Target: db.Human, Body: text, Response: "async", Kind: "task"})
		if err != nil {
			return false, "", 0, 0, err
		}
		tid, err := db.NewTask(d.db, title, db.Human, "alice", 0, db.DefaultPriority, "decision", 0, "")
		if err != nil {
			return false, "", 0, 0, err
		}
		return true, "", rid, tid, db.LinkRequest(d.db, tid, rid)
	}
	ok, said, id, err := team.NewTrust(d.cfg.Home).Propose(root, "the repo", "alice", raise)
	if err != nil || !ok {
		t.Fatal(said, err)
	}
	return d, id, 1, root
}

func answerCard(t *testing.T, d *Daemon, card int64, text string) {
	t.Helper()
	result(t, pipeTo(t, d, "k").ask(fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"inbox.answer","params":{"id":%d,"text":%q,"request_id":"a1"}}`, card, text)))
}

// reply is the answer the proposer is told.
func reply(t *testing.T, d *Daemon, card int64) string {
	t.Helper()
	var body string
	if err := d.db.QueryRow("SELECT body FROM requests WHERE settles = ?", card).Scan(&body); err != nil {
		t.Fatal(err)
	}
	return body
}

func reportsToFounder(t *testing.T, d *Daemon, body string) int {
	t.Helper()
	var n int
	if err := d.db.QueryRow("SELECT COUNT(*) FROM requests WHERE kind = 'say' AND requester = 'alice' AND target = 'human' AND body = ?", body).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

// The founder's approve declares the root as the proposer asked for it, marks the proposal applied and tells the proposer so; the
// founder is reported nothing.
func TestTheFoundersApproveDeclaresTheProposedRoot(t *testing.T) {
	d, id, card, root := trustDaemon(t)
	answerCard(t, d, card, "approve")
	tr := team.NewTrust(d.cfg.Home)
	if got := tr.Declared(); len(got) != 1 || got[0].Root != root || got[0].Why != "the repo" || got[0].AddedBy != "alice" {
		t.Fatalf("%+v", got)
	}
	p, _, _ := tr.LoadProposal(id)
	if p.Get("state") != "applied" || p.Get("verdict") != "approve" || p.Get("applied") == nil {
		t.Fatal(p)
	}
	if want := "\n\nproposal " + id + " applied: " + root + " is now a declared root"; !strings.HasSuffix(reply(t, d, card), want) {
		t.Fatalf("%q", reply(t, d, card))
	}
	var n int
	d.db.QueryRow("SELECT COUNT(*) FROM requests WHERE kind = 'say' AND target = 'human'").Scan(&n)
	if n != 0 {
		t.Fatal("nothing is reported to the founder")
	}
}

// A reject declares nothing: the proposal is settled rejected with the founder's words, and the answer carries no apply line.
func TestTheFoundersRejectSettlesTheProposal(t *testing.T) {
	d, id, card, root := trustDaemon(t)
	answerCard(t, d, card, "no, too wide")
	tr := team.NewTrust(d.cfg.Home)
	if tr.IsDeclared(root) {
		t.Fatal("nothing is declared")
	}
	if p, _, _ := tr.LoadProposal(id); p.Get("state") != "rejected" || p.Get("verdict") != "no, too wide" {
		t.Fatal(p)
	}
	if strings.Contains(reply(t, d, card), "proposal "+id) {
		t.Fatalf("%q", reply(t, d, card))
	}
}

// An approve for a root the founder declared meanwhile is moot: the proposal is withdrawn, never applied, and both the proposer and the
// founder are told it could not be applied.
func TestAnApproveForARootDeclaredMeanwhileIsMoot(t *testing.T) {
	d, id, card, root := trustDaemon(t)
	tr := team.NewTrust(d.cfg.Home)
	tr.Enact(root, "mine")
	answerCard(t, d, card, "yes")
	if got := tr.Declared(); len(got) != 1 || got[0].Why != "mine" {
		t.Fatalf("%+v", got)
	}
	if p, _, _ := tr.LoadProposal(id); p.Get("state") != "withdrawn" || p.Get("verdict") != "moot: that root is already declared" {
		t.Fatal(p)
	}
	line := "proposal " + id + " could not be applied: proposal " + id + " is moot: " + root + " is already a declared root"
	if !strings.HasSuffix(reply(t, d, card), "\n\n"+line) {
		t.Fatalf("%q", reply(t, d, card))
	}
	if reportsToFounder(t, d, line+"\nThe proposal is still open; `hl trust apply "+id+"` retries it.") != 1 {
		t.Fatal("the founder is told it was not applied")
	}
}
