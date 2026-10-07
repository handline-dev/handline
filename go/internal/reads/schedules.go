package reads

import (
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/yamlcompat"
)

type schedule struct{ cron, message string }

func (s schedule) sid() string {
	h := sha1.Sum([]byte(s.cron + "\n" + s.message))
	return hex.EncodeToString(h[:])[:10]
}

// scheduleID is the integer the spec gives a schedule: ten hex digits of sha1 over the agent and the schedule's sid.
func scheduleID(agent string, s schedule) int64 {
	h := sha1.Sum([]byte(agent + "\n" + s.sid()))
	var n int64
	for _, b := range h[:5] {
		n = n<<8 | int64(b)
	}
	return n
}

// pyRepr is repr() of a string: single quotes unless it holds one and no double quote.
func pyRepr(s string) string {
	q := "'"
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		q = `"`
	}
	s = strings.NewReplacer(`\`, `\\`, "\n", `\n`, "\r", `\r`, "\t", `\t`, q, `\`+q).Replace(s)
	return q + s + q
}

// badValue is a `schedules` value that is wrong, said as schedules.parse says it. Anything else that fails reading an agent is not one.
type badValue string

func (b badValue) Error() string { return string(b) }

// jsonError is json.loads's message where the text starts wrong, which is the usual mistake; Go's decoder words the rest.
func jsonError(text string, err error) string {
	t := strings.TrimLeft(text, " \t\r\n")
	literal := strings.HasPrefix(t, "true") || strings.HasPrefix(t, "false") || strings.HasPrefix(t, "null") || strings.HasPrefix(t, "NaN")
	if t == "" || (!literal && !strings.ContainsRune(`{["-0123456789`, rune(t[0]))) {
		return "Expecting value: line 1 column " + fmt.Sprint(len(text)-len(t)+1) + " (char " + fmt.Sprint(len(text)-len(t)) + ")"
	}
	return err.Error()
}

// loadSchedules is an agent's `schedules` value validated as schedules.load does: a badValue says what is wrong with it, any other
// error is an agent.yaml that cannot be read.
func (e *Env) loadSchedules(agent string) ([]schedule, error) {
	cfg, err := team.NewRoster(e.Home).Read(agent)
	if err != nil {
		return nil, err
	}
	var items any
	switch v := cfg["schedules"].(type) {
	case nil:
		return nil, nil
	case string:
		if v == "" {
			return nil, nil
		}
		if err := json.Unmarshal([]byte(v), &items); err != nil {
			return nil, badValue("schedules is not JSON: " + jsonError(v, err))
		}
	default:
		items = v
	}
	list, ok := items.([]any)
	if !ok {
		return nil, badValue("schedules is a list of {cron, message}")
	}
	out := []schedule{}
	for i, it := range list {
		var m map[string]any
		switch x := it.(type) {
		case map[string]any:
			m = x
		case *yamlcompat.OMap:
			m = x.M
		}
		_, hasC := m["cron"]
		_, hasM := m["message"]
		if len(m) != 2 || !hasC || !hasM {
			return nil, badValue(fmt.Sprintf("schedule %d is {cron, message} and nothing else", i+1))
		}
		cronText := pyText(m["cron"])
		if _, err := parseCron(cronText); err != nil {
			return nil, badValue(err.Error())
		}
		message := strings.Join(strings.Fields(pyText(m["message"])), " ")
		if message == "" {
			return nil, badValue(fmt.Sprintf("schedule %d has no message", i+1))
		}
		out = append(out, schedule{strings.Join(strings.Fields(cronText), " "), message})
	}
	return out, nil
}

// firedAt is when a schedule last fired: the newest schedule_fired event of the agent for its cron.
func (e *Env) firedAt(agent string, s schedule) int64 {
	rows, _ := query(e.DB, "SELECT ts, payload FROM events WHERE kind = 'schedule_fired' AND agent = ? ORDER BY id DESC LIMIT 200", agent)
	for _, r := range rows {
		var p map[string]any
		if json.Unmarshal([]byte(r.str("payload")), &p) == nil && p["cron"] == s.cron {
			return r.i("ts")
		}
	}
	return 0
}

// schedulesList is schedules.list: each agent's schedules with the last time one fired and the next time it names, and the agents whose
// value is bad in errors, when there are any.
func (e *Env) schedulesList(c *rpc.Conn, params map[string]any) (any, error) {
	now := time.Now().Unix()
	out, bad := []any{}, []any{}
	for _, agent := range e.listAgents() {
		items, err := e.loadSchedules(agent)
		var b badValue
		if errors.As(err, &b) {
			bad = append(bad, map[string]any{"target": agent, "error": string(b)})
			continue
		}
		if err != nil {
			continue
		}
		for _, s := range items {
			last := e.firedAt(agent, s)
			cur, _ := queryOne(e.DB, "SELECT row_id FROM cursors WHERE name = ?", "schedule_at:"+agent+":"+s.sid())
			handled := cur.i("row_id")
			from := max(last, handled)
			if last == 0 && handled == 0 {
				from = now
			}
			var next, lastRun any
			if n, ok := mustCron(s.cron).nextAfter(from); ok {
				next = ts(n)
			}
			if last != 0 {
				lastRun = ts(last)
			}
			out = append(out, map[string]any{"id": scheduleID(agent, s), "target": agent, "cron": s.cron, "message": s.message,
				"next_run": next, "last_run": lastRun, "set_by": "human"})
		}
	}
	res := map[string]any{"schedules": out}
	if len(bad) > 0 {
		res["errors"] = bad
	}
	return res, nil
}

func mustCron(expr string) *cronExpr {
	c, _ := parseCron(expr)
	return c
}
