package taskwrites

import (
	"database/sql"
	"regexp"

	"github.com/handline/handline/go/internal/db"
)

// humanMention is `@human` in a comment: at the start or after a character that is not part of a name, as the supervisor reads @name.
var humanMention = regexp.MustCompile(`(?:^|[^A-Za-z0-9._@-])@human(?:[^A-Za-z0-9_-]|$)`)

// syncMemoAsk makes the ask to the founder that comment mid on task tid raised match the comment as it reads now: an agent's comment that
// names `@human` has one open ask (its body the comment), and one that does not has none.
func syncMemoAsk(tx *sql.Tx, tid, mid int64, author, body string) error {
	var rid int64
	err := tx.QueryRow("SELECT id FROM requests WHERE memo_id = ? AND state = 'queued'", mid).Scan(&rid)
	if err != nil && err != sql.ErrNoRows {
		return err
	}
	open := err == nil
	switch want := author != human && humanMention.MatchString(body); {
	case want && !open:
		if rid, err = db.AddRequest(tx, db.NewRequest{Requester: author, Target: human, Body: body}); err != nil {
			return err
		}
		if _, err = tx.Exec("UPDATE requests SET memo_id = ? WHERE id = ?", mid, rid); err != nil {
			return err
		}
		return db.LinkRequest(tx, tid, rid)
	case want:
		_, err = tx.Exec("UPDATE requests SET body = ? WHERE id = ?", body, rid)
		return err
	case open:
		return db.CloseMemoAsks(tx, "withdrawn", "memo_id = ?", mid)
	}
	return nil
}

// answerMemoAsks closes the asks to the founder that comments on task tid raised: the founder has now written on the task.
func answerMemoAsks(tx *sql.Tx, tid int64) error {
	return db.CloseMemoAsks(tx, "landed", "id IN (SELECT request_id FROM task_requests WHERE task_id = ?)", tid)
}
