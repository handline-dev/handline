package push

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/stream"
	"github.com/handline/handline/go/internal/usage"
)

func opened(kind, id, asks string, agent any, title string, count int) stream.Entry {
	return stream.Entry{Topic: "for_you", Type: "for_you.opened", Title: title,
		Data: map[string]any{"item": map[string]any{"kind": kind, "id": id, "agent": agent, "title": title, "asks": asks}, "count": count}}
}

func closed(kind, id string, count int) stream.Entry {
	return stream.Entry{Topic: "for_you", Type: "for_you.closed", Data: map[string]any{"kind": kind, "id": id, "count": count}}
}

// An item that joins is a shown note titled by what it asks and who asks, the item title its body, its kind and id what a tap opens and
// its collapse id; the machine asks with no agent. One that leaves is a silent badge update. Another event is no note.
func TestForYouNoteOfEachEvent(t *testing.T) {
	for name, c := range map[string]struct {
		e    stream.Entry
		want Note
		ok   bool
	}{
		"task": {opened("task", "7", "decide", "bob", "Ship the build?", 3),
			Note{Title: "Decide · bob", Body: "Ship the build?", Data: map[string]string{"kind": "task", "id": "7"}, Collapse: "task:7", Badge: 3}, true},
		"prompt": {opened("prompt", "p1", "allow", "ann", "Bash", 1),
			Note{Title: "Allow · ann", Body: "Bash", Data: map[string]string{"kind": "prompt", "id": "p1"}, Collapse: "prompt:p1", Badge: 1}, true},
		"review": {opened("task", "8", "review", "bob", "PR 900", 2),
			Note{Title: "Review · bob", Body: "PR 900", Data: map[string]string{"kind": "task", "id": "8"}, Collapse: "task:8", Badge: 2}, true},
		"machine": {opened("machine", "login", "do", nil, "Log in to sigma", 4),
			Note{Title: "Do by hand", Body: "Log in to sigma", Data: map[string]string{"kind": "machine", "id": "login"}, Collapse: "machine:login", Badge: 4}, true},
		"closed": {closed("task", "7", 2), Note{Badge: 2, Silent: true}, true},
		"other":  {stream.Entry{Topic: "tasks", Type: "task.created", Data: map[string]any{"count": 1}}, Note{}, false},
	} {
		if got, ok := ForYouNote(c.e); ok != c.ok || !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: %+v %v, want %+v", name, got, ok, c.want)
		}
	}
}

// Watch sends each note in order: an item that opens is shown; deferring it to Later closes it (a silent badge); its return from Later
// opens it again and is shown; an event of another topic sends nothing.
func TestWatchSendsTheNoteOfEachForYouEvent(t *testing.T) {
	prod := newFake(t)
	ec, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	s, err := FromConfig(usage.Config{KeyAPNsKey: writeFile(t, "key.p8", writePEM(t, ec)), KeyAPNsKeyID: "KID", KeyAPNsTeamID: "TEAM"}, phones(t))
	if err != nil {
		t.Fatal(err)
	}
	if got := s.Platforms(); !reflect.DeepEqual(got, []string{"apns"}) {
		t.Fatalf("platforms: %v", got)
	}
	a := s.platforms["apns"].(*apns)
	a.client, a.host, a.sandboxHost = prod.Client(), prod.URL, prod.URL

	events, done := make(chan stream.Entry, 8), make(chan struct{})
	for _, e := range []stream.Entry{opened("task", "7", "decide", "bob", "Ship the build?", 1), closed("task", "7", 0),
		{Topic: "tasks", Type: "task.created"}, opened("task", "7", "decide", "bob", "Ship the build?", 1)} {
		events <- e
	}
	close(events)
	go func() { s.Watch(make(chan struct{}), events); close(done) }()
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("Watch did not return when events closed")
	}
	got := prod.take()
	if len(got) != 6 { // two APNs devices, three notes
		t.Fatalf("got %d sends: %+v", len(got), got)
	}
	for i, want := range []string{"10", "10", "5", "5", "10", "10"} { // 5 is a silent note
		if p := got[i].header.Get("apns-priority"); p != want {
			t.Errorf("send %d: priority %s, want %s", i, p, want)
		}
	}
	if aps, _ := got[2].body["aps"].(map[string]any); aps["badge"] != float64(0) || aps["alert"] != nil {
		t.Errorf("the deferred item is a silent badge update: %v", got[2].body)
	}
	if aps, _ := got[4].body["aps"].(map[string]any); got[4].header.Get("apns-collapse-id") != "task:7" || aps["badge"] != float64(1) {
		t.Errorf("the returned item is shown again: %v %v", got[4].header, got[4].body)
	}
}

// Tee appends every event and queues only the for_you ones it appended; with the queue full it logs and the append still succeeds.
func TestTeeQueuesOnlyAppendedForYouEventsAndNeverWaits(t *testing.T) {
	var logged []string
	s := &Sender{Log: func(format string, args ...any) { logged = append(logged, fmt.Sprintf(format, args...)) }}
	var appended []string
	fail := false
	emit := func(e stream.Entry) (*stream.Event, error) {
		if fail {
			return nil, errors.New("disk full")
		}
		appended = append(appended, e.Type)
		return &stream.Event{}, nil
	}
	notes := make(chan stream.Entry, 1)
	tee := s.Tee(emit, notes)
	for _, e := range []stream.Entry{{Topic: "tasks", Type: "task.created"}, closed("task", "7", 0)} {
		if _, err := tee(e); err != nil {
			t.Fatal(err)
		}
	}
	if len(notes) != 1 || (<-notes).Type != "for_you.closed" {
		t.Fatal("only the for_you event is queued")
	}
	fail = true
	if _, err := tee(closed("task", "8", 0)); err == nil || len(notes) != 0 {
		t.Fatalf("an event that was not appended is not queued: %v", err)
	}
	fail = false
	tee(closed("task", "9", 0))
	if _, err := tee(closed("task", "10", 0)); err != nil || len(notes) != 1 || len(appended) != 4 {
		t.Fatalf("a full queue still appends: %v %d %v", err, len(notes), appended)
	}
	if len(logged) != 1 || !strings.Contains(logged[0], "queue is full") {
		t.Errorf("logged: %q", logged)
	}
}

// With push off there is no sender, and it sends to no platform.
func TestNoSenderHasNoPlatforms(t *testing.T) {
	var s *Sender
	if got := s.Platforms(); len(got) != 0 {
		t.Fatalf("%v", got)
	}
}
