package attachments

import (
	"encoding/json"
	"fmt"

	"github.com/handline/handline/go/internal/db"
)

func linkKey(rid int64) string { return fmt.Sprintf("attachments:%d", rid) }

// Record keeps which images the message rid carries, in order.
func Record(q db.Q, rid int64, infos []Info) error {
	b, err := json.Marshal(infos)
	if err != nil {
		return err
	}
	return db.SetMeta(q, linkKey(rid), string(b))
}

// Of is the images message rid carries; none for a message with none.
func Of(q db.Q, rid int64) ([]Info, error) {
	v, ok, err := db.Meta(q, linkKey(rid))
	if err != nil || !ok {
		return nil, err
	}
	var infos []Info
	if err := json.Unmarshal([]byte(v), &infos); err != nil {
		return nil, nil
	}
	return infos, nil
}

// size is a file's size as the path line says it.
func size(n int64) string {
	switch {
	case n < 1024:
		return fmt.Sprintf("%d B", n)
	case n < 1<<20:
		return fmt.Sprintf("%d KB", (n+512)/1024)
	}
	return fmt.Sprintf("%.1f MB", float64(n)/(1<<20))
}

// allSent is the images every message was sent with.
func allSent(q db.Q) ([]Info, error) {
	rows, err := q.Query("SELECT value FROM meta WHERE name LIKE 'attachments:%'")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Info
	for rows.Next() {
		var v string
		if rows.Scan(&v) != nil {
			continue
		}
		var infos []Info
		if json.Unmarshal([]byte(v), &infos) == nil {
			out = append(out, infos...)
		}
	}
	return out, rows.Err()
}
