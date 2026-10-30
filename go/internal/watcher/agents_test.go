package watcher

import (
	"strings"
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

func TestAReadThatFailsAfterTheFirstRowSaysNothing(t *testing.T) {
	d, l := rig(t)
	exec(t, d, "INSERT INTO agent_view(agent, view, updated) VALUES('amy', ?, 0), ('bob', ?, 0)", view("idle"), view("stalled"))
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	a := &Agents{}
	if err := a.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	// the read fails on bob's row, after amy's: a short list must not read as bob gone
	a.query = "SELECT agent, CASE WHEN agent = 'bob' THEN json_extract('{', '$') ELSE view END FROM agent_view"
	if err := a.Scan(d, emit); err == nil {
		t.Fatal("the failed read was swallowed")
	}
	a.query = ""
	if err := a.Scan(d, emit); err != nil {
		t.Fatal(err)
	}
	if got, _ := l.Since(0, []string{"agents", "notifications"}, "", 100); len(got) != 0 {
		t.Fatalf("a failed read said %d things: %+v", len(got), got[0])
	}
}

// A card the daemon raised is a stall although the view says waiting: the notice is titled by the card and carries its title and text.
// A card from the agent's own session is no stall, and a card that stays is told once.
func TestAStallNoticeIsTitledByTheDaemonsCard(t *testing.T) {
	d, l := rig(t)
	card := func(source string) string {
		return `{"name":"bob","state":"waiting","needs_you":{"kind":"dialog","since":"x","title":"Stuck at a dialog","text":"it came back","source":"` + source + `"}}`
	}
	exec(t, d, "INSERT INTO agent_view(agent, view, updated) VALUES('bob', ?, 0)", view("running"))
	a := &Agents{}
	emit := func(e stream.Entry) (*stream.Event, error) { return l.Append(e) }
	stalls := func() []*stream.Event {
		got, err := l.Since(0, []string{"notifications"}, "", 100)
		if err != nil {
			t.Fatal(err)
		}
		return got
	}
	a.Scan(d, emit)
	exec(t, d, "UPDATE agent_view SET view = ? WHERE agent = 'bob'", card("session"))
	if a.Scan(d, emit); len(stalls()) != 0 {
		t.Fatalf("a session's own card is no stall: %+v", stalls())
	}
	exec(t, d, "UPDATE agent_view SET view = ? WHERE agent = 'bob'", card("daemon"))
	a.Scan(d, emit)
	got := stalls()
	if len(got) != 1 || *got[0].Title != "bob: Stuck at a dialog" || string(got[0].Data) != `{"agent":"bob","text":"it came back","title":"Stuck at a dialog"}` {
		t.Fatalf("%+v", got)
	}
	exec(t, d, "UPDATE agent_view SET view = ? WHERE agent = 'bob'", strings.Replace(card("daemon"), "it came back", "again", 1))
	if a.Scan(d, emit); len(stalls()) != 1 {
		t.Fatal("a card that stays is told once")
	}
}
