package watcher

import (
	"testing"

	"github.com/handline/handline/go/internal/stream"
)

func view(state string) string { return `{"name":"bob","state":"` + state + `"}` }

func TestAnAgentViewThatChangesTurnsStalledOrGoesIsToldOnceEach(t *testing.T) {
	d, l := rig(t)
	exec(t, d, "INSERT INTO agent_view(agent, view, updated) VALUES('bob', ?, 0), ('amy', ?, 0)", view("idle"), `{"name":"amy","state":"idle"}`)
	a := &Agents{}
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	all := func() []*stream.Event {
		got, err := l.Since(0, []string{"agents", "notifications"}, "", 100)
		if err != nil {
			t.Fatal(err)
		}
		return got
	}
	if err := a.Scan(d, emit); err != nil || len(all()) != 0 {
		t.Fatalf("the first look said something: %v %v", err, all())
	}
	if err := a.Scan(d, emit); err != nil || len(all()) != 0 {
		t.Fatalf("nothing changed: %v", all())
	}
	exec(t, d, "UPDATE agent_view SET view = ? WHERE agent = 'bob'", view("running"))
	a.Scan(d, emit)
	if got := all(); len(got) != 1 || got[0].Type != "agent.changed" || *got[0].Agent != "bob" || string(got[0].Target) != `{"id":"bob","screen":"agent"}` || got[0].Title != nil {
		t.Fatalf("%+v", got)
	}
	exec(t, d, "UPDATE agent_view SET view = ? WHERE agent = 'bob'", view("stalled"))
	a.Scan(d, emit)
	got := all()
	if len(got) != 3 || got[1].Type != "agent.changed" || got[2].Type != "stall" || got[2].Topic != "notifications" || *got[2].Title != "bob stalled" || string(got[2].Data) != `{"agent":"bob"}` {
		t.Fatalf("%+v", got)
	}
	exec(t, d, "UPDATE agent_view SET view = ? WHERE agent = 'bob'", `{"name":"bob","state":"stalled","detail":"more"}`)
	a.Scan(d, emit)
	if got := all(); len(got) != 4 {
		t.Fatalf("a view that stays stalled is not a new stall: %d rows", len(got))
	}
	exec(t, d, "DELETE FROM agent_view WHERE agent = 'amy'")
	a.Scan(d, emit)
	got = all()
	if last := got[len(got)-1]; len(got) != 5 || last.Type != "agent.removed" || *last.Agent != "amy" || string(last.Data) != `{"name":"amy"}` {
		t.Fatalf("%+v", got)
	}
}
