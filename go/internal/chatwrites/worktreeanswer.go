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

// answerWorktree acts on the founder's answer (1, 2 yes or 3) to the task that asks what to do with a retired member's worktree. handled
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
	card, err := cardOf(tx, tid)
	if err != nil || card == nil {
		return false, "", err
	}
	path, member := match(pathLine, card.Body), match(memberLine, card.Body)
	if path == "" || member == "" {
		return false, "", nil
	}
	fields := strings.Fields(strings.TrimSpace(words))
	if len(fields) == 0 {
		return true, "", rpc.Refused("answer 1 (keep a copy, then remove), 2 yes (remove anyway) or 3 (leave it)", "state", "")
	}
	switch fields[0] {
	case "1":
		branch, err := worktreeprune.Rescue(path, member, now)
		if err != nil {
			return true, "", rpc.Refused(err.Error(), "state", "")
		}
		return true, fmt.Sprintf("1: what was left is on %s; %s is removed", branch, path), nil
	case "2":
		if len(fields) < 2 || (fields[1] != "yes" && fields[1] != "예") {
			return true, "", rpc.Refused("2 removes "+path+" with its uncommitted files for good: answer \"2 yes\" to confirm", "state", "")
		}
		if ok, why := worktreeprune.RemoveForce(path); !ok {
			return true, "", rpc.Refused("not removed: "+why, "state", "")
		}
		return true, "2 yes: " + path + " is removed", nil
	case "3":
		return true, "3: " + path + " stays", nil
	}
	return true, "", rpc.Refused("answer 1 (keep a copy, then remove), 2 yes (remove anyway) or 3 (leave it)", "state", "")
}

func match(re *regexp.Regexp, s string) string {
	if m := re.FindStringSubmatch(s); m != nil {
		return strings.TrimSpace(m[1])
	}
	return ""
}
