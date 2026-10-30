// Package cmdruns keeps the runs of a message's command blocks (shell.open with a command and a block): one row of the meta table per block, so
// no migration. A run starts "running"; Settle ends it from what the terminal host says of its window.
package cmdruns

import (
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/term"
)

const prefix = "run:"

// keep is how long a run row stays after it ends.
const keep = 30 * 24 * 3600

var blockRE = regexp.MustCompile(`^([0-9]{1,18}):([0-9]{1,9})$`)

// Block is a parsed "<message id>:<n>".
type Block struct{ Message, N int64 }

// ParseBlock reads "<message id>:<n>"; ok is false for any other text.
func ParseBlock(s string) (Block, bool) {
	m := blockRE.FindStringSubmatch(s)
	if m == nil {
		return Block{}, false
	}
	msg, _ := strconv.ParseInt(m[1], 10, 64)
	n, _ := strconv.ParseInt(m[2], 10, 64)
	return Block{msg, n}, true
}

func (b Block) String() string { return fmt.Sprintf("%d:%d", b.Message, b.N) }

// Run is one run as kept: State is running, done, failed or stopped; Exit is set once it is done or failed.
type Run struct {
	Block    string `json:"block"`
	Terminal string `json:"terminal"`
	Started  int64  `json:"started"`
	Ended    int64  `json:"ended,omitempty"`
	State    string `json:"state"`
	Exit     *int   `json:"exit,omitempty"`
}

// Tag is what the window carries to tell it from a later pane with the same id.
func (r Run) Tag() string { return r.Block + "@" + strconv.FormatInt(r.Started, 10) }

// Seconds is how long it ran; -1 while it has not ended or when it ended unread.
func (r Run) Seconds() int64 {
	if r.Exit == nil {
		return -1
	}
	return max(r.Ended-r.Started, 0)
}

// Start records the run of block in terminal, replacing the one the block had.
func Start(q db.Q, b Block, terminal string, started int64) (Run, error) {
	r := Run{Block: b.String(), Terminal: terminal, Started: started, State: "running"}
	enc, _ := json.Marshal(r)
	_, err := q.Exec("INSERT INTO meta(name, value, updated) VALUES(?, ?, ?) ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated=excluded.updated",
		prefix+r.Block, string(enc), started)
	return r, err
}

func decode(name, value string) (Run, bool) {
	var r Run
	if json.Unmarshal([]byte(value), &r) != nil || r.Block != strings.TrimPrefix(name, prefix) {
		return Run{}, false
	}
	return r, true
}

func read(q db.Q, where string, args ...any) ([]Run, error) {
	rows, err := q.Query("SELECT name, COALESCE(value, '') FROM meta WHERE name LIKE ? ORDER BY name", append([]any{where}, args...)...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Run
	for rows.Next() {
		var name, value string
		if err := rows.Scan(&name, &value); err != nil {
			return nil, err
		}
		if r, ok := decode(name, value); ok {
			out = append(out, r)
		}
	}
	return out, rows.Err()
}

// All is every kept run.
func All(q db.Q) ([]Run, error) { return read(q, prefix+"%") }

// Of is the runs of message id, oldest start first.
func Of(q db.Q, id int64) ([]Run, error) {
	out, err := read(q, prefix+strconv.FormatInt(id, 10)+":%")
	sort.SliceStable(out, func(i, j int) bool { return out[i].Started < out[j].Started })
	return out, err
}

// Settle ends every running run the host's answer shows over: its window is dead (exit status and end time are tmux's own), or gone, or is
// another pane now (stopped). panes nil is "could not ask": nothing ends. Rows that ended long ago are dropped.
func Settle(q db.Q, panes map[string]term.PaneRun, now int64) error {
	runs, err := All(q)
	if err != nil {
		return err
	}
	for _, r := range runs {
		if r.State != "running" {
			if r.Ended > 0 && now-r.Ended > keep {
				if _, err := q.Exec("DELETE FROM meta WHERE name = ?", prefix+r.Block); err != nil {
					return err
				}
			}
			continue
		}
		if panes == nil {
			continue
		}
		p, here := panes[r.Terminal]
		switch {
		case !here || p.Tag != r.Tag():
			r.State, r.Ended = "stopped", now
		case p.Dead:
			st := p.Status
			r.Exit, r.Ended = &st, p.DeadTime
			if r.Ended == 0 {
				r.Ended = now
			}
			r.State = "done"
			if st != 0 {
				r.State = "failed"
			}
		default:
			continue
		}
		enc, _ := json.Marshal(r)
		if _, err := q.Exec("UPDATE meta SET value = ?, updated = ? WHERE name = ?", string(enc), now, prefix+r.Block); err != nil {
			return err
		}
	}
	return nil
}

// Now is the clock the daemon stamps a run with.
func Now() int64 { return time.Now().Unix() }
