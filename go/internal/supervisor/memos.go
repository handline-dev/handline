package supervisor

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
)

// MemoAdd adds by's memo to a task (actions.memo_add): (ok, message, memo id). The holder is told by the caller, once its transaction has
// committed (TellMemoHolder).
func (e *Env) MemoAdd(q db.Q, tid int64, by, body string) (ok bool, msg string, mid int64, err error) {
	defer Catch(&err)
	if must(db.GetTask(q, tid)) == nil {
		return false, fmt.Sprintf("no task T%d", tid), 0, nil
	}
	if strings.TrimSpace(body) == "" {
		return false, "a memo needs some text", 0, nil
	}
	mid = must(db.AddMemo(q, tid, by, body))
	return true, fmt.Sprintf("memo %d added to T%d", mid, tid), mid, nil
}

// memoGuard: a memo is anyone's to add; only its author or the founder edits or deletes it.
func memoGuard(m *db.Memo, by string) string {
	if by == db.Human || by == m.Author {
		return ""
	}
	return fmt.Sprintf("memo %d is %s's: only its author or the founder edits or deletes it", m.ID, m.Author)
}

// MemoEdit replaces a memo's text and stamps edited_at; the same text is no edit. The task is returned for the holder's telling.
func (e *Env) MemoEdit(q db.Q, mid int64, by, body string) (ok bool, msg string, tid int64, err error) {
	defer Catch(&err)
	m := must(db.GetMemo(q, mid))
	if m == nil {
		return false, fmt.Sprintf("no memo %d", mid), 0, nil
	}
	if why := memoGuard(m, by); why != "" {
		return false, why, 0, nil
	}
	if strings.TrimSpace(body) == "" {
		return false, "a memo needs some text", 0, nil
	}
	if body == m.Body {
		return false, fmt.Sprintf("memo %d already reads that way - nothing to edit", mid), 0, nil
	}
	check(db.EditMemo(q, mid, body))
	return true, fmt.Sprintf("memo %d on T%d edited", mid, m.TaskID), m.TaskID, nil
}

// MemoDelete removes a memo. The task is returned for the holder's telling.
func (e *Env) MemoDelete(q db.Q, mid int64, by string) (ok bool, msg string, tid int64, err error) {
	defer Catch(&err)
	m := must(db.GetMemo(q, mid))
	if m == nil {
		return false, fmt.Sprintf("no memo %d", mid), 0, nil
	}
	if why := memoGuard(m, by); why != "" {
		return false, why, 0, nil
	}
	check(db.DeleteMemo(q, mid))
	return true, fmt.Sprintf("memo %d deleted from T%d", mid, m.TaskID), m.TaskID, nil
}
