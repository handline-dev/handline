package reads

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
)

func TestAnAgentIsSentOnlyWhatItsOwnReadsWouldReturn(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "worker", "")
	addAgent(t, e, "other", "")
	for _, q := range []string{
		`INSERT INTO stream_log(ts, topic, type, agent, data) VALUES(1, 'notifications', 'prompt_asked', 'other', '{"command":"rm -rf /secret"}')`,
		`INSERT INTO stream_log(ts, topic, type, agent, data) VALUES(1, 'notifications', 'stall', 'worker', '{}')`,
		`INSERT INTO stream_log(ts, topic, type, agent, data) VALUES(1, 'notifications', 'something_new', 'worker', '{}')`,
		`INSERT INTO stream_log(ts, topic, type, agent, data) VALUES(1, 'inbox', 'inbox.added', 'other', '{}')`,
		`INSERT INTO stream_log(ts, topic, type, agent, data) VALUES(1, 'inbox', 'inbox.added', 'worker', '{}')`,
		`INSERT INTO stream_log(ts, topic, type, agent, data) VALUES(1, 'tasks', 'task.changed', 'other', '{}')`,
		`INSERT INTO stream_log(ts, topic, type, agent, data) VALUES(1, 'tasks', 'task.changed', 'worker', '{}')`,
		`INSERT INTO stream_log(ts, topic, type, agent, data) VALUES(1, 'hr', 'hr.changed', NULL, '{}')`,
	} {
		if _, err := e.DB.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	feed := func(c *rpc.Conn) (events []map[string]any, cursor float64) {
		got, err := e.eventsFeed(c, map[string]any{"topics": []any{"notifications", "inbox", "tasks", "hr"}, "since": json.Number("0")})
		if err != nil {
			t.Fatal(err)
		}
		b, _ := json.Marshal(got)
		var out struct {
			Events []map[string]any
			Cursor float64
		}
		json.Unmarshal(b, &out)
		return out.Events, out.Cursor
	}
	agent := &rpc.Conn{Caller: rpc.Caller{Role: "agent", Info: map[string]any{"kind": "agent", "name": "worker"}}}
	got, cursor := feed(agent)
	var seen []string
	for _, ev := range got {
		seen = append(seen, ev["topic"].(string)+" "+ev["type"].(string))
	}
	if want := "notifications stall,inbox inbox.added,tasks task.changed,hr hr.changed"; strings.Join(seen, ",") != want {
		t.Fatalf("%v", seen)
	}
	member := &rpc.Conn{Caller: rpc.Caller{Role: "founder", Info: map[string]any{"kind": "person"}}}
	all, top := feed(member)
	if len(all) != 8 || cursor != top {
		t.Fatalf("a member gets all %d, and the cursor is where the log was read to (%v, %v)", len(all), cursor, top)
	}
}
