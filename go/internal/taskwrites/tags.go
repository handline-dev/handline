package taskwrites

import (
	"database/sql"
	"fmt"
	"regexp"
	"strings"

	"github.com/handline/handline/go/internal/rpc"
)

// maxTags is the most tags one task carries.
const maxTags = 20

// tagShape is a tag: a short lowercase word, so it stays one chip and one filter term.
var tagShape = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,31}$`)

// TagList is the tags under key, lowercased and without repeats; nil when the key is absent.
func TagList(p map[string]any, key string) ([]string, error) {
	v, has := p[key]
	if !has || v == nil {
		return nil, nil
	}
	items, ok := v.([]any)
	if !ok {
		return nil, badParam(key+" is a list of tags", key)
	}
	seen := map[string]bool{}
	out := []string{}
	for _, it := range items {
		s, _ := it.(string)
		tag := strings.ToLower(strings.TrimSpace(s))
		if !tagShape.MatchString(tag) {
			return nil, badParam(fmt.Sprintf("%s is not a tag: up to 32 letters, digits, '.', '_' or '-', starting with a letter or digit",
				rpc.PyJSON(it, false, false)), key)
		}
		if !seen[tag] {
			seen[tag] = true
			out = append(out, tag)
		}
	}
	return out, nil
}

// retag adds and removes tags of task tid, kept as one edit_task event under by. It answers the refusal ("" when it changed) and the message.
func retag(tx *sql.Tx, tid int64, by string, add, remove []string) (string, string, error) {
	if ok, err := hasTask(tx, tid); err != nil || !ok {
		return fmt.Sprintf("no task T%d", tid), "", err
	}
	has := map[string]bool{}
	rows, err := tx.Query("SELECT tag FROM task_tags WHERE task_id = ?", tid)
	if err != nil {
		return "", "", err
	}
	for rows.Next() {
		var tag string
		if err := rows.Scan(&tag); err != nil {
			rows.Close()
			return "", "", err
		}
		has[tag] = true
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return "", "", err
	}
	added, removed := []any{}, []any{}
	var said []string
	for _, tag := range remove {
		if has[tag] {
			delete(has, tag)
			removed = append(removed, tag)
			said = append(said, "-"+tag)
		}
	}
	for _, tag := range add {
		if !has[tag] {
			has[tag] = true
			added = append(added, tag)
			said = append(said, "+"+tag)
		}
	}
	if len(said) == 0 {
		return fmt.Sprintf("task T%d already has those tags - nothing to change", tid), "", nil
	}
	if len(has) > maxTags {
		return fmt.Sprintf("a task carries at most %d tags - T%d would have %d", maxTags, tid, len(has)), "", nil
	}
	for _, tag := range removed {
		if _, err := tx.Exec("DELETE FROM task_tags WHERE task_id = ? AND tag = ?", tid, tag); err != nil {
			return "", "", err
		}
	}
	for _, tag := range added {
		if _, err := tx.Exec("INSERT INTO task_tags(task_id, tag) VALUES(?, ?)", tid, tag); err != nil {
			return "", "", err
		}
	}
	if err := addEvent(tx, by, "edit_task", kv{"task_id", tid}, kv{"tags", rpc.Pairs{{K: "add", V: added}, {K: "remove", V: removed}}}); err != nil {
		return "", "", err
	}
	return "", fmt.Sprintf("task T%d tags %s", tid, strings.Join(said, " ")), nil
}
