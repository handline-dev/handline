package chatwrites

import (
	"database/sql"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/sparenotice"
	"github.com/handline/handline/go/internal/taskwrites"
)

var tasksLine = regexp.MustCompile(`(?m)^Tasks: (.+)$`)

// answerSpare acts on the founder's answer to a spare-task notice: "start" queues the tasks it lists, which go through the same holds as
// any queued task. handled is false for any other task and for any other answer, which dismisses the notice as it would close any task of
// the founder's; queued are the tasks it queued, to be delivered after the commit.
func answerSpare(q db.Q, home string, tid int64, words string) (handled bool, said string, queued []int64, err error) {
	var tagged int
	if err := q.QueryRow("SELECT COUNT(*) FROM task_tags WHERE task_id = ? AND tag = ?", tid, sparenotice.NoticeTag).Scan(&tagged); err != nil || tagged == 0 {
		return false, "", nil, err
	}
	t, err := db.GetTask(q, tid)
	if err != nil || t == nil || t.Ended() || t.OpenedBy != db.Human { // tags are free text: only the daemon's own notice, opened as the founder's
		return false, "", nil, err
	}
	card, err := cardOf(q, tid)
	if err != nil || card == nil {
		return false, "", nil, err
	}
	list := match(tasksLine, card.Body)
	if list == "" { // a notice with no spare task listed offers nothing to start: it is closed with the founder's words
		return false, "", nil, nil
	}
	if fields := strings.Fields(strings.ToLower(words)); len(fields) == 0 || (fields[0] != "start" && fields[0] != "시작") {
		return false, "", nil, nil // any other answer dismisses the notice, as it closes any task for the founder
	}
	var ids []int64
	for _, f := range strings.FieldsFunc(list, func(r rune) bool { return r == ',' || r == ' ' }) {
		if n, err := strconv.ParseInt(strings.TrimPrefix(f, "T"), 10, 64); err == nil {
			ids = append(ids, n)
		}
	}
	tx, ok := q.(*sql.Tx)
	if !ok {
		return true, "", nil, fmt.Errorf("starting the spare tasks of T%d needs the call's transaction", tid)
	}
	queued, skipped, err := taskwrites.QueueDrafts(tx, home, db.Human, ids)
	if err != nil {
		return true, "", nil, err
	}
	said = "start: queued " + sparenotice.TaskList(queued)
	if len(queued) == 0 {
		said = "start: nothing was queued"
	}
	if len(skipped) > 0 {
		said += "; skipped " + strings.Join(skipped, ", ")
	}
	return true, said, queued, nil
}
