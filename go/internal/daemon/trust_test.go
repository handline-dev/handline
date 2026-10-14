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

// foundersApproval is the founder's approve of proposal id as answering it records it, with its apply left for later: the answer on
// record for the decision task, and the task closed with it by the founder.
func foundersApproval(t *testing.T, d *Daemon, id string, card int64, root string) (tr *team.Trust, tid int64) {
	t.Helper()
	tid, err := db.TaskOfCard(d.db, card)
	if err != nil || tid == 0 {
		t.Fatal(tid, err)
	}
	tr = team.NewTrust(d.cfg.Home)
	recordAnswer(t, tr, id, tid, root)
	closeDecision(t, d, tid, db.Human, "settle_decision_task")
	return tr, tid
}

// recordAnswer records the founder's approve of proposal id, answered on decision task tid for subject.
func recordAnswer(t *testing.T, tr *team.Trust, id string, tid int64, subject string) {
	t.Helper()
	if err := tr.Store.Put("trust-decisions", id, fmt.Sprintf(`{"task": %d, "call": "approved", "subject": %q, "at": 1}`, tid, subject)); err != nil {
		t.Fatal(err)
	}
}

// closeDecision marks decision task tid answered "approve", with the event of whoever closed it.
func closeDecision(t *testing.T, d *Daemon, tid int64, by, kind string) {
	t.Helper()
	if _, err := d.db.Exec("UPDATE tasks SET done_evidence = 'approve' WHERE id = ?", tid); err != nil {
		t.Fatal(err)
	}
	if _, err := db.AddEvent(d.db, by, kind, db.KV{K: "task_id", V: tid}); err != nil {
		t.Fatal(err)
	}
}

func setProposalRoot(t *testing.T, tr *team.Trust, id, root string) {
	t.Helper()
	p, _, err := tr.LoadProposal(id)
	if err != nil {
		t.Fatal(err)
	}
	for i := range p {
		if p[i].Key == "root" {
			p[i].Val = root
		}
	}
	if err := tr.SaveProposal(p); err != nil {
		t.Fatal(err)
	}
}

// What is declared is the root on the card the founder read: a card rewritten to its parent after the answer declares nothing.
func TestAnApproveDeclaresNothingWhenTheCardNoLongerNamesTheRoot(t *testing.T) {
	d, id, card, root := trustDaemon(t)
	tr, _ := foundersApproval(t, d, id, card, root)
	if _, err := d.db.Exec("UPDATE requests SET body = replace(body, ?, ?) WHERE id = ?", root, filepath.Dir(root), card); err != nil {
		t.Fatal(err)
	}
	ok, said, err := tr.Apply(d.db, id)
	if err != nil || ok || !strings.Contains(said, fmt.Sprintf("does not match what #%d asked the founder to approve", card)) {
		t.Fatal(ok, said, err)
	}
	if len(tr.Declared()) != 0 {
		t.Fatalf("%+v", tr.Declared())
	}
}

// The founder's recorded answer binds the draft: a proposal edited to the root's parent afterwards, or an answer recorded for another
// decision task, is refused; the draft as answered still applies.
func TestAnApproveIsForTheDraftAndTaskTheFounderAnswered(t *testing.T) {
	d, id, card, root := trustDaemon(t)
	tr, tid := foundersApproval(t, d, id, card, root)
	setProposalRoot(t, tr, id, filepath.Dir(root))
	if ok, said, err := tr.Apply(d.db, id); err != nil || ok || !strings.Contains(said, "was changed after the founder answered it") {
		t.Fatal(ok, said, err)
	}
	setProposalRoot(t, tr, id, root)
	recordAnswer(t, tr, id, tid+1, root)
	if ok, said, err := tr.Apply(d.db, id); err != nil || ok || !strings.Contains(said, fmt.Sprintf("is not for T%d", tid)) {
		t.Fatal(ok, said, err)
	}
	if len(tr.Declared()) != 0 {
		t.Fatalf("%+v", tr.Declared())
	}
	recordAnswer(t, tr, id, tid, root)
	if ok, said, err := tr.Apply(d.db, id); err != nil || !ok || !tr.IsDeclared(root) {
		t.Fatal(ok, said, err)
	}
}

// Only the founder's close is an answer: a decision an agent marked done, or one reopened after the founder closed it, decided nothing.
func TestADecisionNotClosedByTheFounderDecidesNothing(t *testing.T) {
	for name, after := range map[string]string{"marked done by an agent": "mark_task_done", "reopened": "reopen_task"} {
		t.Run(name, func(t *testing.T) {
			d, id, card, root := trustDaemon(t)
			tr, tid := foundersApproval(t, d, id, card, root)
			closeDecision(t, d, tid, "alice", after)
			if ok, said, err := tr.Apply(d.db, id); err != nil || ok || !strings.Contains(said, "nothing was decided") {
				t.Fatal(ok, said, err)
			}
			if tr.IsDeclared(root) {
				t.Fatal("nothing is declared")
			}
		})
	}
}
