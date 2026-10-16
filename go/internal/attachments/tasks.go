package attachments

import (
	"fmt"
	"os"
	"regexp"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
)

// refPattern is how a text names an image (contract x-attachments, texts): ![caption](attachment:id). Anything else is plain text.
var refPattern = regexp.MustCompile(`!\[([^\]]*)\]\(attachment:([^)]*)\)`)

// Ref is one image a text names.
type Ref struct {
	Caption, ID string
	Start, End  int // the reference's bytes in the text
}

// Refs is the images text names, in order.
func Refs(text string) []Ref {
	var out []Ref
	for _, m := range refPattern.FindAllStringSubmatchIndex(text, -1) {
		out = append(out, Ref{Caption: text[m[2]:m[3]], ID: text[m[4]:m[5]], Start: m[0], End: m[1]})
	}
	return out
}

// taskTexts are the texts that may name an image, each with its task: its brief (the first request linked to it), its memos and the notes
// it was handed on or sent back with. The LIKE filters scan every such row, which is fine at today's sizes.
const taskTexts = `SELECT l.task_id AS tid, r.body AS text FROM task_requests l JOIN requests r ON r.id = l.request_id
	WHERE l.rowid = (SELECT MIN(f.rowid) FROM task_requests f WHERE f.task_id = l.task_id) AND r.body LIKE '%](attachment:%'
UNION ALL SELECT task_id, body FROM task_memos WHERE body LIKE '%](attachment:%'
UNION ALL SELECT json_extract(payload, '$.task_id'), json_extract(payload, '$.note') FROM events
	WHERE kind IN ('reject_task', 'task_handoff') AND json_valid(payload) AND payload LIKE '%](attachment:%'`

// Held is every image a task names, with the time it is kept from: now while a task naming it is open, else when the last task naming it
// was done or discarded.
func Held(q db.Q, now int64) (map[string]int64, error) {
	rows, err := q.Query(`SELECT x.text, CASE WHEN COALESCE(t.done_evidence, '') = '' AND COALESCE(t.discarded_by, '') = '' THEN ?
		ELSE MAX(COALESCE(t.done_at, 0), COALESCE(t.discarded_at, 0)) END FROM (`+taskTexts+`) x JOIN tasks t ON t.id = x.tid`, now)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	held := map[string]int64{}
	for rows.Next() {
		var text string
		var from int64
		if err := rows.Scan(&text, &from); err != nil {
			return nil, err
		}
		for _, r := range Refs(text) {
			held[r.ID] = max(held[r.ID], from)
		}
	}
	return held, rows.Err()
}

// Check is what a write that sets field to text may name: at most MaxPerMessage images, each finished and still kept, and for an agent
// (which cannot upload) only one a task or a message already names. An image the replaced text (was) names is not checked again. It
// answers the ids to claim.
func (s *Store) Check(q db.Q, text, was, field string, agent bool) ([]string, error) {
	refs := Refs(text)
	if len(refs) > MaxPerMessage {
		return nil, badText(fmt.Sprintf("%s names %d images: at most %d", field, len(refs), MaxPerMessage), field)
	}
	kept := map[string]bool{}
	for _, r := range Refs(was) {
		kept[r.ID] = true
	}
	var named map[string]bool
	var ids []string
	for _, r := range refs {
		if kept[r.ID] {
			continue
		}
		kept[r.ID] = true
		if err := s.checkID(r.ID, field); err != nil {
			return nil, err
		}
		if agent {
			if named == nil {
				var err error
				if named, err = Named(q); err != nil {
					return nil, err
				}
			}
			if !named[r.ID] {
				return nil, rpc.Refused(fmt.Sprintf("attachment:%s is named by no task or message: an agent may name only an image one already shows", r.ID), "role", "")
			}
		}
		ids = append(ids, r.ID)
	}
	return ids, nil
}

// checkID is whether a write may name id in field: well formed, its upload finished, still kept.
func (s *Store) checkID(id, field string) error {
	if !ValidID(id) {
		return badText(fmt.Sprintf("attachment:%s in %s is not an attachment id", id, field), field)
	}
	if _, err := os.Stat(s.sub("uploads", id+".json")); err == nil {
		return badText(fmt.Sprintf("attachment:%s in %s is an upload that has not finished", id, field), field)
	}
	_, err := s.Lookup(id)
	return err
}

// Named is every image a task or a message names: what an agent may pass on.
func Named(q db.Q) (map[string]bool, error) {
	held, err := Held(q, 0)
	if err != nil {
		return nil, err
	}
	named := map[string]bool{}
	for id := range held {
		named[id] = true
	}
	rows, err := q.Query(`SELECT body FROM requests WHERE body LIKE '%](attachment:%'`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var body string
		if err := rows.Scan(&body); err != nil {
			return nil, err
		}
		for _, r := range Refs(body) {
			named[r.ID] = true
		}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	sent, err := allSent(q)
	for _, in := range sent {
		named[in.ID] = true
	}
	return named, err
}

func badText(reason, field string) *rpc.Error {
	e := badParam(reason, field)
	e.Data["reason"] = reason
	return e
}
