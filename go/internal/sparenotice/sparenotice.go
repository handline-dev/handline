// Package sparenotice tells the founder when an account will lose weekly use at its reset and lists the spare tasks that could use it (T6335).
// A spare task is a draft tagged "spare". The notice is a task for the founder tagged "spare-notice"; answering it with "start" queues the
// listed tasks, which then wait for the 5-hour and load holds like any task.
package sparenotice

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/usage"
)

const (
	// SpareTag marks a draft task that is useful whenever capacity is free; NoticeTag marks the founder's task that offers them.
	SpareTag, NoticeTag = "spare", "spare-notice"
	// MinLeft is the least forecast loss, in points of the week, worth a notice; HorizonH is how near the reset must be.
	MinLeft, HorizonH = 10.0, 48.0
	// Every is how long one account is told of at most, unless its loss doubles.
	Every = 12 * time.Hour
	// MostListed is how many spare tasks a notice names.
	MostListed = 6
)

// Env is what the sweep reads and does.
type Env struct {
	Q        db.Q
	Accounts func() []string
	// Forecast is the account's forecast, ok false where its week cannot be read (usage.Env.ForecastFor).
	Forecast func(acct string) (usage.Forecast, bool)
	// ResetAt is when the account's week ends, unix seconds.
	ResetAt func(acct string) int64
	// Waiting is whether a queued task waits for the account: an agent on it with work not yet reached.
	Waiting func(acct string) bool
	// Ask opens the founder's task; Tell sends the same words to handline.
	Ask  func(title, body string, tags ...string) (int64, error)
	Tell func(text string) error
	Now  func() time.Time
	Log  func(format string, a ...any)
}

type told struct {
	At   int64   `json:"at"`
	Left float64 `json:"left"`
}

func metaKey(acct string) string { return "spare_notice:" + acct }

// Sweep sends the notice for each account that will lose a tenth of its week or more within two days with nothing queued for it.
func (e *Env) Sweep(int64) error {
	for _, acct := range e.Accounts() {
		f, ok := e.Forecast(acct)
		if !ok || f.LeftAtReset == nil || *f.LeftAtReset < MinLeft || f.HoursLeft > HorizonH || f.HoursLeft <= 0 || e.Waiting(acct) {
			continue
		}
		if !e.due(acct, *f.LeftAtReset) {
			continue
		}
		if err := e.notify(acct, *f.LeftAtReset, e.ResetAt(acct)); err != nil {
			return fmt.Errorf("%s: %w", acct, err)
		}
	}
	return nil
}

// due is whether the account is to be told: never told, last told over Every ago, or its loss has doubled since.
func (e *Env) due(acct string, left float64) bool {
	raw, ok, err := db.Meta(e.Q, metaKey(acct))
	if err != nil || !ok {
		return err == nil
	}
	var last told
	if json.Unmarshal([]byte(raw), &last) != nil {
		return true
	}
	return e.Now().Sub(time.Unix(last.At, 0)) >= Every || left >= 2*last.Left
}

// Spare is the numbers of the open draft tasks tagged spare, the founder's own left out, lowest number first.
func Spare(q db.Q, limit int) ([]int64, error) {
	rows, err := q.Query("SELECT t.id FROM tasks t JOIN task_tags g ON g.task_id = t.id AND g.tag = ? WHERE t.done_evidence IS NULL AND "+
		"t.discarded_by IS NULL AND t.queued_by IS NULL AND t.owner != ? ORDER BY t.id LIMIT ?", SpareTag, db.Human, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		out = append(out, id)
	}
	return out, rows.Err()
}

// Words is the notice, for the founder's task title and the message to handline.
func Words(acct string, left float64, at time.Time, spare []int64) string {
	text := fmt.Sprintf("%.0f%% of %s's weekly use will be lost at %s.", left, acct, at.Local().Format("15:04"))
	if len(spare) == 0 {
		return text + " No spare task is waiting: tag drafts \"spare\" so there is work for it."
	}
	return text + " These spare tasks can use it: " + TaskList(spare) + "."
}

// TaskList writes task numbers as "T1, T2".
func TaskList(ids []int64) string {
	names := make([]string, len(ids))
	for i, id := range ids {
		names[i] = fmt.Sprintf("T%d", id)
	}
	return strings.Join(names, ", ")
}

func (e *Env) notify(acct string, left float64, resetAt int64) error {
	spare, err := Spare(e.Q, MostListed)
	if err != nil {
		return err
	}
	text := Words(acct, left, time.Unix(resetAt, 0), spare)
	body := text + "\nAccount: " + acct
	if len(spare) > 0 {
		body += "\nTasks: " + TaskList(spare) + "\nAnswer start to queue them. They wait for the 5-hour and load holds like any task."
	}
	if _, err := e.Ask(text, body, NoticeTag); err != nil {
		return err
	}
	if e.Tell != nil {
		if err := e.Tell(text); err != nil && e.Log != nil {
			e.Log("  spare notice: telling handline failed: %v", err)
		}
	}
	b, _ := json.Marshal(told{At: e.Now().Unix(), Left: left})
	return db.SetMeta(e.Q, metaKey(acct), string(b))
}
