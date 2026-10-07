package devices

import (
	"database/sql"
)

// SQLStore keeps devices in the daemon's database, in the devices table. Revoking a device deletes its row.
type SQLStore struct{ DB *sql.DB }

func (s SQLStore) Add(d Device, tokenHash string) error {
	_, err := s.DB.Exec("INSERT INTO devices(id, name, token_hash, created, last_seen) VALUES(?,?,?,?,NULL)", d.ID, d.Name, tokenHash, d.Created)
	return err
}

func (s SQLStore) ByTokenHash(hash string) (Device, bool, error) {
	var d Device
	var seen sql.NullInt64
	err := s.DB.QueryRow("SELECT id, name, created, last_seen FROM devices WHERE token_hash = ?", hash).Scan(&d.ID, &d.Name, &d.Created, &seen)
	if err == sql.ErrNoRows {
		return Device{}, false, nil
	}
	if seen.Valid {
		d.LastSeen = &seen.Int64
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
	rows, err := s.DB.Query("SELECT id, name, created, last_seen FROM devices ORDER BY created, id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Device{}
	for rows.Next() {
		var d Device
		var seen sql.NullInt64
		if err := rows.Scan(&d.ID, &d.Name, &d.Created, &seen); err != nil {
			return nil, err
		}
		if seen.Valid {
			d.LastSeen = &seen.Int64
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

func (s SQLStore) Touch(id string, ts int64) error {
	_, err := s.DB.Exec("UPDATE devices SET last_seen = ? WHERE id = ?", ts, id)
	return err
}
