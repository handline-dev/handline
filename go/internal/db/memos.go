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

// AddMemo is db.add_memo. The stamp is a float, as Python's time.time() writes it.
func AddMemo(q Q, tid int64, author, body string) (int64, error) {
	res, err := q.Exec("INSERT INTO task_memos(task_id, author, body, created_at) VALUES (?, ?, ?, ?)", tid, author, body, float64(time.Now().UnixNano())/1e9)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
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

// EditMemo is db.edit_memo.
func EditMemo(q Q, mid int64, body string) error {
	_, err := q.Exec("UPDATE task_memos SET body = ?, edited_at = ? WHERE id = ?", body, Now(), mid)
	return err
}

// DeleteMemo is db.delete_memo.
func DeleteMemo(q Q, mid int64) error {
	_, err := q.Exec("DELETE FROM task_memos WHERE id = ?", mid)
	return err
}
