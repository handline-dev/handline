package db

import (
	"database/sql"
	"time"
)

// Memo is one task_memos row.
type Memo struct {
	ID, TaskID   int64
	Author, Body string
	CreatedAt    float64
	EditedAt     sql.NullFloat64
}

// A memo's add, edit and delete are each kept as an event (memo_add, memo_edit, memo_delete) with the text before and after: they are the
// task's history (task.history), which keeps a deleted memo's words. q should be the transaction that changes the memo.

// AddMemo is db.add_memo. The stamp is a float, as Python's time.time() writes it.
func AddMemo(q Q, tid int64, author, body string) (int64, error) {
	res, err := q.Exec("INSERT INTO task_memos(task_id, author, body, created_at) VALUES (?, ?, ?, ?)", tid, author, body, float64(time.Now().UnixNano())/1e9)
	if err != nil {
		return 0, err
	}
	mid, err := res.LastInsertId()
	if err != nil {
		return 0, err
	}
	_, err = AddEvent(q, author, "memo_add", KV{"task_id", tid}, KV{"memo", mid}, KV{"now", body})
	return mid, err
}

// GetMemo is db.get_memo: nil when there is no such memo.
func GetMemo(q Q, mid int64) (*Memo, error) {
	m := &Memo{}
	err := q.QueryRow("SELECT id, task_id, author, body, created_at, edited_at FROM task_memos WHERE id = ?", mid).
		Scan(&m.ID, &m.TaskID, &m.Author, &m.Body, &m.CreatedAt, &m.EditedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return m, err
}

// EditMemo is db.edit_memo: by replaces memo mid's text.
func EditMemo(q Q, mid int64, by, body string) error {
	m, err := GetMemo(q, mid)
	if err != nil || m == nil {
		return err
	}
	if _, err := q.Exec("UPDATE task_memos SET body = ?, edited_at = ? WHERE id = ?", body, Now(), mid); err != nil {
		return err
	}
	_, err = AddEvent(q, by, "memo_edit", KV{"task_id", m.TaskID}, KV{"memo", mid}, KV{"was", m.Body}, KV{"now", body})
	return err
}

// DeleteMemo is db.delete_memo: by removes memo mid.
func DeleteMemo(q Q, mid int64, by string) error {
	m, err := GetMemo(q, mid)
	if err != nil || m == nil {
		return err
	}
	if _, err := q.Exec("DELETE FROM task_memos WHERE id = ?", mid); err != nil {
		return err
	}
	_, err = AddEvent(q, by, "memo_delete", KV{"task_id", m.TaskID}, KV{"memo", mid}, KV{"was", m.Body})
	return err
}

// MemoTexts is the texts of T<tid>'s memos, oldest first.
func MemoTexts(q Q, tid int64) ([]string, error) {
	rows, err := q.Query("SELECT body FROM task_memos WHERE task_id = ? ORDER BY id", tid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var body string
		if err := rows.Scan(&body); err != nil {
			return nil, err
		}
		out = append(out, body)
	}
	return out, rows.Err()
}

// LinkMemoTell keeps that request rid told reader of memo mid.
func LinkMemoTell(q Q, mid int64, reader string, rid int64) error {
	_, err := q.Exec("INSERT OR REPLACE INTO memo_tells(memo_id, reader, request_id) VALUES (?, ?, ?)", mid, reader, rid)
	return err
}
