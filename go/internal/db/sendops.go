package db

import (
	"database/sql"
	"encoding/json"
)

// SendOp is one message to a structured session as the session's ledger keeps it (structured.DBLedger): the operations row
// `send:<agent>:<op>`. found is false when the session never recorded the operation.
func SendOp(q Q, agent, op string) (state, uuid string, found bool, err error) {
	var st, res sql.NullString
	err = q.QueryRow("SELECT state, result_json FROM operations WHERE id = ? AND kind = 'send'", "send:"+agent+":"+op).Scan(&st, &res)
	if err == sql.ErrNoRows {
		return "", "", false, nil
	}
	if err != nil {
		return "", "", false, err
	}
	var r struct {
		UUID string `json:"uuid"`
	}
	json.Unmarshal([]byte(res.String), &r)
	return st.String, r.UUID, true, nil
}

// SetSendOpState moves an operation from one state to another and says whether it was in from: the move of a session that is not running.
func SetSendOpState(q Q, agent, op, from, to string) (bool, error) {
	res, err := q.Exec("UPDATE operations SET state = ?, updated_at = ? WHERE id = ? AND kind = 'send' AND state = ?",
		to, Now(), "send:"+agent+":"+op, from)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}
