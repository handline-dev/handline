package chatwrites

import (
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/worktreeprune"
)

var (
	pathLine   = regexp.MustCompile(`(?m)^Path: (.+)$`)
	memberLine = regexp.MustCompile(`(?m)^Member: (.+)$`)
)

// answerWorktree acts on the founder's answer (keep, save or force; 3, 1 or 2 yes on older tasks) to the task that asks what to do with a retired member's worktree. handled
// is false for any other task. A refused answer or a git failure leaves the task open; the act is done before the task closes, so a
// removal that did not happen is never reported as one. said is what the task's verdict records.
func answerWorktree(tx db.Q, tid int64, words string, now time.Time) (handled bool, said string, err error) {
	var tagged int
	if err := tx.QueryRow("SELECT COUNT(*) FROM task_tags WHERE task_id = ? AND tag = ?", tid, worktreeprune.Tag).Scan(&tagged); err != nil || tagged == 0 {
		return false, "", err
	}
	t, err := db.GetTask(tx, tid)
	if err != nil || t == nil || t.Ended() {
		return false, "", err
	}
	if t.OpenedBy != db.Human { // tags are free text: only the daemon's own question, opened as the founder's, is one
		return false, "", nil
	}
	card, err := cardOf(tx, tid)
	if err != nil || card == nil {
		return false, "", err
	}
	path, member := match(pathLine, card.Body), match(memberLine, card.Body)
	if path == "" || member == "" {
		return false, "", nil
	}
	if !registered(path) {
		return true, "", rpc.Refused(path+" is not a worktree git knows beside its main checkout: nothing was done", "state", "")
	}
	fields := strings.Fields(strings.TrimSpace(words))
	if len(fields) == 0 {
		return true, "", rpc.Refused(answers, "state", "")
	}
	switch fields[0] { // the numbers are the answers of tasks raised before the words
	case worktreeprune.Save, "1":
		branch, err := worktreeprune.Rescue(path, member, now)
		if err != nil {
			return true, "", rpc.Refused(err.Error(), "state", "")
		}
		return true, fmt.Sprintf("saved: what was left is on %s; %s is removed", branch, path), nil
	case worktreeprune.Force, "2":
		if fields[0] == "2" && (len(fields) < 2 || (fields[1] != "yes" && fields[1] != "예")) {
			return true, "", rpc.Refused("2 removes "+path+" with its uncommitted files for good: answer \"2 yes\" or force to confirm", "state", "")
		}
		if ok, why := worktreeprune.RemoveForce(path); !ok {
			return true, "", rpc.Refused("not removed: "+why, "state", "")
		}
		return true, "force deleted: " + path + " is removed with what it held", nil
	case worktreeprune.Keep, "3":
		return true, "kept: " + path + " stays", nil
	}
	return true, "", rpc.Refused(answers, "state", "")
}

// answers says what the worktree task takes.
const answers = "answer keep (the worktree stays), save (commit what is left to a rescue branch, then remove it) or force (remove it with what it holds)"

func match(re *regexp.Regexp, s string) string {
	if m := re.FindStringSubmatch(s); m != nil {
		return strings.TrimSpace(m[1])
	}
	return ""
}

// registered is whether path is one of its repository's worktrees and not the main checkout (git lists that first).
func registered(path string) bool {
	all := worktreeprune.List(path)
	for i, wt := range all {
		if i > 0 && worktreeprune.Real(wt.Path) == worktreeprune.Real(path) {
			return true
		}
	}
	return false
}
