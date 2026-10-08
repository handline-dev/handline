package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
)

// blockerRefusal is _blocker_refusal: why blocksOn cannot be waited on, or "".
func blockerRefusal(tx *sql.Tx, blocksOn int64) (string, error) {
	t, err := getTask(tx, blocksOn)
	switch {
	case err != nil:
		return "", err
	case t == nil:
		return fmt.Sprintf("no task T%d", blocksOn), nil
	case t.ended():
		return fmt.Sprintf("task T%d is %s", blocksOn, t.endWord()), nil
	}
	return "", nil
}

func (t *task) ended() bool { return t.done() || t.DiscardedBy.Valid && t.DiscardedBy.String != "" }

// endWord is how an ended task is said to have ended: discarded wins over done.
func (t *task) endWord() string {
	if t.DiscardedBy.Valid && t.DiscardedBy.String != "" {
		return "discarded"
	}
	return "done"
}

// blockerGuard is _blocker_guard: whoever may edit the task, or its owner, who knows what it waits for, may record or clear its waits.
func blockerGuard(t *task, by string) string {
	if by == t.Owner {
		return ""
	}
	return editGuard(&mergeRow{ID: t.ID, OpenedBy: t.OpenedBy}, by)
}

// cyclePath is _cycle_path: the waits from blocksOn back to tid that tid waiting on blocksOn would close into a loop, nil when it would not.
func cyclePath(tx *sql.Tx, tid, blocksOn int64) ([]int64, error) {
	seen := map[int64]bool{}
	var walk func(node int64) ([]int64, error)
	walk = func(node int64) ([]int64, error) {
		if seen[node] {
			return nil, nil
		}
		seen[node] = true
		nexts, err := db.BlocksOnIDs(tx, node)
		if err != nil {
			return nil, err
		}
		for _, n := range nexts {
			if n == tid {
				return []int64{node}, nil
			}
		}
		for _, n := range nexts {
			found, err := walk(n)
			if err != nil || found != nil {
				return append([]int64{node}, found...), err
			}
		}
		return nil, nil
	}
	return walk(blocksOn)
}

// recordBlock is actions.add_block: tid cannot be worked until blocksOn is done. It answers the refusal, "" when the wait was recorded.
func recordBlock(tx *sql.Tx, tid, blocksOn int64, by string) (string, error) {
	t, err := getTask(tx, tid)
	if err != nil {
		return "", err
	}
	if t == nil {
		return fmt.Sprintf("no task T%d", tid), nil
	}
	if g := blockerGuard(t, by); g != "" {
		return g, nil
	}
	if on, err := getTask(tx, blocksOn); err != nil {
		return "", err
	} else if on == nil {
		return fmt.Sprintf("no task T%d", blocksOn), nil
	}
	if t.ended() {
		return fmt.Sprintf("task T%d is %s", tid, t.endWord()), nil
	}
	if gone, err := blockerRefusal(tx, blocksOn); err != nil || gone != "" {
		return gone, err
	}
	if blocksOn == tid {
		return fmt.Sprintf("task T%d cannot block itself", tid), nil
	}
	loop, err := cyclePath(tx, tid, blocksOn)
	if err != nil {
		return "", err
	}
	if loop != nil {
		ring := []string{fmt.Sprintf("T%d", tid)}
		for _, n := range loop {
			ring = append(ring, fmt.Sprintf("T%d", n))
		}
		ring = append(ring, fmt.Sprintf("T%d", tid))
		return fmt.Sprintf("task T%d cannot wait on T%d: that would be a cycle, %s", tid, blocksOn, strings.Join(ring, " -> ")), nil
	}
	if err := db.AddBlock(tx, tid, blocksOn); err != nil {
		return "", err
	}
	return "", addEvent(tx, by, "block_task", kv{"task_id", tid}, kv{"blocks_on", blocksOn})
}

// unblock is actions.unblock: one waiting edge removed. It answers the refusal, "" when it was.
func unblock(tx *sql.Tx, tid, blocksOn int64, by string) (string, error) {
	t, err := getTask(tx, tid)
	if err != nil {
		return "", err
	}
	if t == nil {
		return fmt.Sprintf("no task T%d", tid), nil
	}
	if g := blockerGuard(t, by); g != "" {
		return g, nil
	}
	gone, err := db.RemoveBlock(tx, tid, blocksOn)
	if err != nil {
		return "", err
	}
	if !gone {
		return fmt.Sprintf("task T%d is not waiting on T%d", tid, blocksOn), nil
	}
	newest, err := newestRequest(tx)
	if err != nil {
		return "", err
	}
	return "", addEvent(tx, by, "unblock_task", kv{"task_id", tid}, kv{"blocks_on", blocksOn}, kv{"after_request", newest})
}
