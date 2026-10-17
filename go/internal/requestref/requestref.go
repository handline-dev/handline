// Package requestref turns what a caller names a request by (its integer id, or its ref) into the id, for a viewer who may read it.
// It sits below reads and the write packages so every request-naming param goes through the same rule.
package requestref

import (
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
)

// MinPrefix is the shortest ref prefix taken; a ref is 12 hex characters.
const MinPrefix = 6

var refShape = regexp.MustCompile(`^[0-9a-f]{6,12}$`)

// Viewer is who reads as the caller: an agent its own name, the founder and a device the person.
func Viewer(c *rpc.Conn) string {
	if c != nil && c.Caller.Role == "agent" {
		return c.Caller.Info["name"].(string)
	}
	return db.Human
}

// MayRead is whether viewer may read request id: the founder, its requester or target, either one's supervisor, the assignee, owner or
// opener of a task linked to it, and the target of a message the founder sent under one of the founder's own (a quote).
func MayRead(q db.Q, home, viewer string, id int64, requester, target string) bool {
	if viewer == db.Human || viewer == requester || viewer == target {
		return true
	}
	t, _ := team.LoadTeam(home)
	if t.SupervisorOf(requester) == viewer || t.SupervisorOf(target) == viewer {
		return true
	}
	var linked int
	q.QueryRow("SELECT (SELECT COUNT(*) FROM task_requests l JOIN tasks t ON t.id = l.task_id WHERE l.request_id = ?1 AND "+
		"(t.owner = ?2 OR t.opened_by = ?2 OR t.reporter = ?2)) + (SELECT COUNT(*) FROM requests q WHERE q.parent_id = ?1 AND q.target = ?2 AND "+
		"q.requester = ?3)", id, viewer, db.Human).Scan(&linked)
	return linked > 0
}

// Resolve is the id of the request value names in field: a JSON integer is an id; a string is a ref, #ref, or a prefix of at least
// MinPrefix characters matched only among the requests viewer may read. One viewer may not read is not_found, like one that does not exist.
func Resolve(q db.Q, home string, value any, viewer, field string) (int64, error) {
	if id, ok := rpc.AsInt(value); ok {
		var requester, target string
		err := q.QueryRow("SELECT requester, target FROM requests WHERE id = ?", id).Scan(&requester, &target)
		if err != nil || !MayRead(q, home, viewer, id, requester, target) {
			n := strconv.FormatInt(id, 10)
			return 0, noRequest(n, n)
		}
		return id, nil
	}
	s, ok := value.(string)
	ref := strings.ToLower(strings.TrimPrefix(strings.TrimSpace(s), "#"))
	if !ok || !refShape.MatchString(ref) {
		return 0, rpc.BadRequestError(fmt.Sprintf("%s is a request number, or its ref (#ref, or %d to 12 of its hex characters)", field, MinPrefix),
			field, rpc.InvalidParams)
	}
	rows, err := q.Query("SELECT id, requester, target FROM requests WHERE ref >= ? AND ref < ? ORDER BY id", ref, ref+"g")
	if err != nil {
		return 0, err
	}
	type cand struct {
		id                int64
		requester, target string
	}
	var all []cand
	for rows.Next() {
		var c cand
		if err := rows.Scan(&c.id, &c.requester, &c.target); err != nil {
			rows.Close()
			return 0, err
		}
		all = append(all, c)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, err
	}
	var found []int64
	for _, c := range all {
		if MayRead(q, home, viewer, c.id, c.requester, c.target) {
			found = append(found, c.id)
		}
	}
	switch len(found) {
	case 0:
		return 0, noRequest(ref, "#"+ref)
	case 1:
		return found[0], nil
	}
	return 0, rpc.BadRequestError(fmt.Sprintf("#%s is ambiguous: it begins %d requests you may read; give more of the ref", ref, len(found)), field, rpc.InvalidParams)
}

// Missing is the id or ref a caller gave that Resolve found no request of its for, so a method answers it as it answers a missing one.
func Missing(err error) (string, bool) {
	var e *rpc.Error
	if errors.As(err, &e) && e.Code == "not_found" && e.Data["what"] == "request" {
		name, _ := e.Data["name"].(string)
		return name, true
	}
	return "", false
}

func noRequest(name, shown string) *rpc.Error {
	err := rpc.NotFoundError("no request " + shown)
	err.Data = map[string]any{"what": "request", "name": name}
	return err
}
