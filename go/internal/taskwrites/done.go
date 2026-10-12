package taskwrites

import (
	"database/sql"
	"fmt"
	"regexp"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/taskresult"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

var shaLike = regexp.MustCompile(`^[0-9a-fA-F]{4,40}$`)

// doneMethod is task.done (actions.mark_task_done): the owner's or the founder's call, with exactly one of a merge sha on main, a
// request already answered, or a one-line fact.
func doneMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, err := taskID(p)
		if err != nil {
			return nil, err
		}
		var evidence *string
		if v := p["evidence"]; v != nil {
			s, ok := v.(string)
			if !ok || strings.TrimSpace(s) == "" {
				return nil, badParam("evidence is text and not empty", "evidence")
			}
			evidence = &s
		}
		var request *int64
		if v := p["request"]; v != nil {
			n, ok := rpc.AsInt(v)
			if !ok {
				return nil, badParam("request is a request number", "request")
			}
			request = &n
		}
		if (evidence == nil) == (request == nil) {
			return nil, badParam("done takes exactly one of evidence or request", "evidence")
		}
		forceWords := false
		if v := p["reason"]; v != nil {
			b, ok := v.(bool)
			if !ok {
				return nil, badParam("reason is true or false", "reason")
			}
			forceWords = b
		}
		in := DoneIn{Task: tid}
		if in.Commit, in.Shots, err = resultParams(p); err != nil {
			return nil, err
		}
		by := c.Caller.Actor()
		var sha, words string
		if evidence != nil {
			if !forceWords && shaLike.MatchString(*evidence) {
				sha = *evidence
			} else {
				words = *evidence
			}
		}
		repo := ""
		if v := p["repo"]; v != nil {
			s, ok := v.(string)
			if !ok {
				return nil, badParam("repo is text", "repo")
			}
			repo = s
		}
		if repo == "" && (sha != "" || in.Commit != "") {
			repo = team.NewRoster(home).RepoOf(by)
		}
		if (sha != "" || in.Commit != "") && repo == "" {
			return nil, badParam("a sha is checked against a repo: name one", "repo")
		}
		var done *DoneResult
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			in.Sha, in.Words, in.Request, in.Repo = sha, words, request, repo
			res, err := MarkDone(tx, home, by, in)
			if err != nil {
				return nil, err
			}
			done = res
			return accepted(res.Said, nil), nil
		})
		if err == nil && done != nil && done.Keep != nil {
			if kerr := done.Keep(); kerr != nil {
				return nil, fmt.Errorf("task T%d is done, but its screenshots could not be put in place: %w", tid, kerr)
			}
		}
		if err == nil && done != nil && tell != nil {
			tell.Done(tid, by, done.Owner, done.Told, done.Forget, done.Finished)
		}
		return out, err
	}
}

// DoneIn is what MarkDone is given: exactly one of Sha (checked against Repo's main), Request (already answered) or Words.
type DoneIn struct {
	Task       int64
	Sha, Words string
	Request    *int64
	Repo       string
	// Commit is the sha the work was done at (it need not be on main); Shots are absolute paths of PNG files. Both are optional.
	Commit string
	Shots  []string
}

// DoneResult is what a MarkDone leaves for the telling that follows the commit. Finished is false when only a leftover request of an
// already done task was settled.
type DoneResult struct {
	Said     string
	Owner    string
	Told     []db.Notice
	Forget   []db.InboxFile
	Finished bool
	// Keep puts the task's screenshots in place; the caller runs it once the end has committed. nil when there are none.
	Keep func() error
}

// MarkDone is actions.mark_task_done in the caller's transaction: a refusal is the error, nothing is written then. The caller hands the
// result to Teller.Done once it has committed.
func MarkDone(tx *sql.Tx, home, by string, in DoneIn) (*DoneResult, error) {
	tid, sha, words, request, repo := in.Task, in.Sha, in.Words, in.Request, in.Repo
	t, err := getTask(tx, tid)
	if err != nil {
		return nil, err
	}
	if t == nil {
		return nil, refusal(fmt.Sprintf("no task T%d", tid))
	}
	if t.DiscardedBy.Valid && t.DiscardedBy.String != "" {
		return nil, refusal(fmt.Sprintf("task T%d is already discarded (%s)", tid, t.DiscardedBy.String))
	}
	if t.done() {
		// a delivery left queued when the task ended some other way is settled here
		reqs, err := taskRequests(tx, tid)
		if err != nil {
			return nil, err
		}
		for _, r := range reqs {
			if r.State == "queued" && (by == t.Owner || by == human) {
				notes, forget, err := endTask(tx, home, tid, by, t, doneText(tid, by, t.DoneEvidence.String), urgent(t.DoneEvidence.String), nil)
				if err != nil {
					return nil, err
				}
				return &DoneResult{Said: fmt.Sprintf("task T%d is already done (%s); its leftover request is settled", tid, t.DoneEvidence.String),
					Owner: t.Owner, Told: notes, Forget: forget}, nil
			}
		}
		return nil, refusal(fmt.Sprintf("task T%d is already done (%s)", tid, t.DoneEvidence.String))
	}
	// a sha-expecting task's opener or reporter may close it another way when the sha is never going to land
	shaOverride := (by == t.OpenedBy || by == t.reporter()) && t.Expects.String == "sha"
	if by != t.Owner && by != human && !shaOverride {
		return nil, refusal(fmt.Sprintf("task T%d can only be marked done by its owner (%s) or the founder", tid, t.Owner))
	}
	var mark, via string
	switch {
	case sha != "":
		full, why := shaOnMain(repo, sha)
		if full == "" {
			if why != "" {
				why = " (" + why + ")"
			}
			return nil, refusal(fmt.Sprintf("%s is not an ancestor of main%s", sha, why))
		}
		mark, via = full, "sha"
	case request != nil:
		var state string
		switch err := tx.QueryRow("SELECT state FROM requests WHERE id = ?", *request).Scan(&state); {
		case err == sql.ErrNoRows:
			return nil, refusal(fmt.Sprintf("no request #%d", *request))
		case err != nil:
			return nil, err
		}
		if state == "queued" {
			return nil, refusal(fmt.Sprintf("request #%d is still queued - not answered yet", *request))
		}
		mark, via = fmt.Sprintf("request #%d", *request), "request"
	default:
		mark, via = words, "reason"
	}
	if t.Expects.String == "sha" && via != "sha" && by != t.OpenedBy && by != t.reporter() && by != human {
		return nil, refusal(fmt.Sprintf("task T%d expects a sha - `hl task done T%d <sha>`, or its opener (%s or the founder may close it another way", tid, tid, openerAndReporter(t.OpenedBy, t.reporter())))
	}
	kids, err := openChildren(tx, tid)
	if err != nil {
		return nil, err
	}
	if len(kids) > 0 {
		return nil, refusal(fmt.Sprintf("task T%d still has open child tasks (%s) - it is done once they are", tid, taskList(kids)))
	}
	blockers, err := taskBlockers(tx, tid)
	if err != nil {
		return nil, err
	}
	if len(blockers) > 0 && by != human {
		var waits []int64
		for _, b := range blockers {
			waits = append(waits, b.ID)
		}
		return nil, refusal(fmt.Sprintf("task T%d is waiting on %s - it is done only once they are", tid, taskList(waits)))
	}
	result, finalize, err := doneResult(tx, home, by, t, in, via, mark)
	if err != nil {
		return nil, err
	}
	from, err := state(tx, t)
	if err != nil {
		return nil, err
	}
	won, err := db.MarkTaskDone(tx, tid, mark, via)
	if err != nil {
		return nil, err
	}
	if !won {
		return nil, refusal(fmt.Sprintf("task T%d was marked done by someone else just now", tid))
	}
	if err := addEvent(tx, by, "mark_task_done", kv{"task_id", tid}, kv{"done", mark}, kv{"from", from}); err != nil {
		return nil, err
	}
	if !result.Empty() {
		if err := taskresult.Save(tx, tid, result); err != nil {
			return nil, err
		}
	}
	notes, forget, err := endTask(tx, home, tid, by, t, doneText(tid, by, mark), urgent(mark), nil)
	if err != nil {
		return nil, err
	}
	return &DoneResult{Said: fmt.Sprintf("task T%d done (%s)", tid, mark), Owner: t.Owner, Told: notes, Forget: forget, Finished: true, Keep: finalize}, nil
}

// doneText is what a task's end says to whoever it tells: "T<n> done by <by>: <evidence on one line>".
func doneText(tid int64, by, evidence string) string {
	who := by
	if by == human {
		who = "the founder"
	}
	return strings.TrimRight(fmt.Sprintf("T%d done by %s: %s", tid, who, strings.Join(strings.Fields(evidence), " ")), ": ")
}

// shaOnMain is _sha_on_main: the full sha `sha` names in repo when main reaches it, else "" and why. A sha typed by hand that never
// merged must not mark a task done, so this runs before anything is written, and what is stored is the full sha.
func shaOnMain(repo, sha string) (full, why string) {
	r := gitrun.Run(repo, "rev-parse", "--verify", "--quiet", sha+"^{commit}")
	if r.RC < 0 {
		return "", r.Stderr
	}
	if r.RC != 0 {
		return "", "no such commit in this repo"
	}
	full = strings.TrimSpace(r.Stdout)
	r = gitrun.Run(repo, "merge-base", "--is-ancestor", full, gitrun.IntegrationBranch)
	switch {
	case r.RC < 0:
		return "", r.Stderr
	case r.RC == 0:
		return full, ""
	}
	msg := r.Stderr
	if msg == "" {
		msg = r.Stdout
	}
	return "", textsafe.Oneline(strings.TrimSpace(msg))
}
