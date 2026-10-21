package daemon

import (
	"fmt"
	"testing"

	"github.com/handline/handline/go/internal/stream"
)

func founderOn(t *testing.T, d *Daemon) *client {
	t.Helper()
	c := dial(t, d)
	if r := c.raw(t, `{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t/1","protocol":1,"key":"k"}}`); r["result"] == nil {
		t.Fatalf("hello: %v", r)
	}
	return c
}

func appendTasks(t *testing.T, d *Daemon) int64 {
	t.Helper()
	ev, err := d.hub.Append(stream.Entry{Topic: "tasks", Type: "task.changed", Data: map[string]any{"id": 1}})
	if err != nil {
		t.Fatal(err)
	}
	return ev.Cursor
}

// resume latest answers the newest cursor and replays nothing: the first event after it is the next one appended. replay, and no
// resume, replay what came after since.
func TestAResumeAtTheLatestSkipsWhatWasMissed(t *testing.T) {
	d := start(t, fakeAuth{key: "k"}, "")
	appendTasks(t, d)
	missed := appendTasks(t, d)
	for _, c := range []struct{ resume, first string }{{`,"resume":"latest"`, "next"}, {`,"resume":"replay"`, "missed"}, {"", "missed"}} {
		fo := founderOn(t, d)
		r := fo.raw(t, `{"jsonrpc":"2.0","id":2,"method":"subscribe","params":{"topics":["tasks"],"since":1`+c.resume+`}}`)
		res, _ := r["result"].(map[string]any)
		if res == nil || res["cursor"].(float64) < float64(missed) {
			t.Fatalf("%s: %v", c.resume, r)
		}
		next := appendTasks(t, d)
		want := map[string]int64{"next": next, "missed": missed}[c.first]
		if got := fo.read(t)["params"].(map[string]any)["cursor"]; got != float64(want) {
			t.Errorf("%q: the first event is %v, want %d", c.resume, got, want)
		}
	}
	fo := founderOn(t, d)
	if code, data := errOf(t, fo.raw(t, `{"jsonrpc":"2.0","id":2,"method":"subscribe","params":{"topics":["tasks"],"resume":"all"}}`)); code != -32602 ||
		data != "bad_request" {
		t.Errorf("resume all: %d %s", code, data)
	}
}

// Every Stamped read is served here, and one read says the newest cursor at the time it was read.
func TestAScreenReadSaysTheCursorItWasReadAt(t *testing.T) {
	d := start(t, fakeAuth{key: "k"}, "")
	for _, name := range stream.Stamped {
		if !d.local.Has(name) {
			t.Errorf("%s is stamped but not served", name)
		}
	}
	top := appendTasks(t, d)
	fo := founderOn(t, d)
	for i, method := range []string{"daemon.status", "agents.list", "tasks.list", "inbox.list", "accounts.list", "prefs.get"} {
		r := fo.raw(t, fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":%q,"params":{}}`, i+2, method))
		res, _ := r["result"].(map[string]any)
		if res == nil || res["cursor"] != float64(top) {
			t.Errorf("%s: %v", method, r)
		}
	}
}
