package devices

import (
	"database/sql"
)

// SQLStore keeps devices in the daemon's database, in the devices table. Revoking a device deletes its row, and its push token with it.
type SQLStore struct{ DB *sql.DB }

func (s SQLStore) Add(d Device, tokenHash string) error {
	_, err := s.DB.Exec("INSERT INTO devices(id, name, token_hash, created, last_seen) VALUES(?,?,?,?,NULL)", d.ID, d.Name, tokenHash, d.Created)
	return err
}

const columns = "id, name, created, last_seen, push_platform, push_token, push_sandbox"

type scanner interface{ Scan(dest ...any) error }

func scan(r scanner) (Device, error) {
	var d Device
	var seen, sandbox sql.NullInt64
	var platform, token sql.NullString
	if err := r.Scan(&d.ID, &d.Name, &d.Created, &seen, &platform, &token, &sandbox); err != nil {
		return Device{}, err
	}
	if seen.Valid {
		d.LastSeen = &seen.Int64
	}
	if token.String != "" {
		d.Push = &Push{Platform: platform.String, Token: token.String, Sandbox: sandbox.Int64 != 0}
	}
	return d, nil
}

func (s SQLStore) ByTokenHash(hash string) (Device, bool, error) {
	d, err := scan(s.DB.QueryRow("SELECT "+columns+" FROM devices WHERE token_hash = ?", hash))
	if err == sql.ErrNoRows {
		return Device{}, false, nil
	}
	return d, err == nil, err
}

func (s SQLStore) Delete(id string) (bool, error) {
	r, err := s.DB.Exec("DELETE FROM devices WHERE id = ?", id)
	if err != nil {
		return false, err
	}
	n, err := r.RowsAffected()
	return n > 0, err
}

func (s SQLStore) List() ([]Device, error) {
	rows, err := s.DB.Query("SELECT " + columns + " FROM devices ORDER BY created, id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Device{}
	for rows.Next() {
		d, err := scan(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

func (s SQLStore) Touch(id string, ts int64) error {
	_, err := s.DB.Exec("UPDATE devices SET last_seen = ? WHERE id = ?", ts, id)
	return err
}

func (s SQLStore) SetPush(id string, p Push) (bool, error) {
	r, err := s.DB.Exec("UPDATE devices SET push_platform = ?, push_token = ?, push_sandbox = ? WHERE id = ?", p.Platform, p.Token, p.Sandbox, id)
	if err != nil {
		return false, err
	}
	n, err := r.RowsAffected()
	return n > 0, err
}

func (s SQLStore) DropPush(id, token string) error {
	_, err := s.DB.Exec("UPDATE devices SET push_platform = NULL, push_token = NULL, push_sandbox = NULL WHERE id = ? AND push_token = ?", id, token)
	return err
}
