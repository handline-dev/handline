package attachments

import (
	"fmt"
	"os"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

// Emit puts one event on a topic (stream_log), for attachment.removed.
type Emit func(topic, typ string, data map[string]any) error

// tombstone is what is left of an image the founder removed: a read of its id then says gone and removed.
type tombstone struct {
	RemovedAt int64  `json:"removed_at"`
	By        string `json:"by"`
}

// Removed is whether the founder removed image id (attachment.remove).
func (s *Store) Removed(id string) bool {
	_, err := os.Stat(s.sub("removed", id+".json"))
	return err == nil
}

// Remove deletes image id at once and leaves its tombstone, by whom, and answers how many places name it (Naming). An id removed before
// is already, and nothing is done; dry counts and changes nothing. An id never put, or one retention removed, is not_found.
func (s *Store) Remove(q db.Q, id, by string, dry bool) (n int, already bool, err error) {
	if !ValidID(id) {
		return 0, false, badParam("id is an attachment id", "id")
	}
	if s.Removed(id) {
		return 0, true, nil
	}
	info, err := s.Lookup(id)
	if err != nil {
		return 0, false, err
	}
	if n, err = Naming(q, id); err != nil || dry {
		return n, false, err
	}
	if err := s.ensure(); err != nil {
		return 0, false, err
	}
	if err := writeJSON(s.sub("removed", id+".json"), tombstone{RemovedAt: s.now().Unix(), By: by}); err != nil {
		return 0, false, err
	}
	s.remove(info)
	return n, false, nil
}

// naming is every message (a chat message or a task's brief), memo and handoff or send-back note that names an image or carries it,
// each once: ?1 is the reference's LIKE pattern, ?2 the carried id's.
const naming = `SELECT count(*) FROM (
	SELECT 'r' || id FROM requests WHERE body LIKE ?1
	UNION SELECT 'r' || substr(name, 13) FROM meta WHERE name LIKE 'attachments:%' AND value LIKE ?2
	UNION SELECT 'm' || id FROM task_memos WHERE body LIKE ?1
	UNION SELECT 'm' || substr(name, 12) FROM meta WHERE name LIKE 'memo-files:%' AND value LIKE ?2
	UNION SELECT 'e' || rowid FROM events WHERE kind IN ('reject_task', 'task_handoff') AND json_valid(payload)
		AND json_extract(payload, '$.note') LIKE ?1)`

// Naming is how many places name image id or carry it: attachment.remove's removed_from.
func Naming(q db.Q, id string) (int, error) {
	var n int
	err := q.QueryRow(naming, "%](attachment:"+id+")%", `%"id":"`+id+`"%`).Scan(&n)
	return n, err
}

// removeMethod is attachment.remove: the founder deletes an image everywhere it is named, after a dry run that counts the places.
func (s *Store) removeMethod(emit Emit) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		id, ok := p["id"].(string)
		if !ok || !ValidID(id) {
			return nil, badParam("id is an attachment id", "id")
		}
		dry := false
		if v := p["dry_run"]; v != nil {
			if dry, ok = v.(bool); !ok {
				return nil, badParam("dry_run is true or false", "dry_run")
			}
		}
		n, already, err := s.Remove(c.Server().DB, id, c.Caller.Actor(), dry)
		switch {
		case err != nil:
			return nil, err
		case already:
			return map[string]any{"accepted": true, "said": "It was deleted already.", "removed_from": 0}, nil
		case dry:
			return map[string]any{"accepted": true, "said": "Deletes it from " + places(n) + ".", "removed_from": n}, nil
		}
		if err := c.RecordWrite("attachment.remove", p["request_id"].(string)); err != nil {
			return nil, err
		}
		for _, topic := range []string{"chat", "tasks"} {
			if err := emit(topic, "attachment.removed", map[string]any{"id": id}); err != nil {
				return nil, err
			}
		}
		return map[string]any{"accepted": true, "said": "Deleted from " + places(n) + ".", "removed_from": n}, nil
	}
}

func places(n int) string {
	if n == 1 {
		return "1 place"
	}
	return fmt.Sprintf("%d places", n)
}
