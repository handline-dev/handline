package attachments

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func TestRemoveDeletesTheFileAtOnceAndLeavesATombstone(t *testing.T) {
	s, now, q := testStore(t)
	img := putAll(t, s, q, "a.png", "image/png", pngOf(2, 2), 1000)
	expired := putAll(t, s, q, "e.png", "image/png", pngOf(2, 2), 1000)
	s.remove(*expired)

	if _, already, err := s.Remove(q, img.ID, "founder", true); err != nil || already {
		t.Fatalf("a dry run: %v %v", already, err)
	}
	if _, err := os.Stat(s.Path(*img)); err != nil || s.Removed(img.ID) {
		t.Fatalf("a dry run keeps the file and leaves no tombstone: %v", err)
	}

	if _, already, err := s.Remove(q, img.ID, "founder", false); err != nil || already {
		t.Fatalf("the remove: %v %v", already, err)
	}
	if _, err := os.Stat(s.Path(*img)); !os.IsNotExist(err) {
		t.Fatalf("the file is deleted at once: %v", err)
	}
	var tomb tombstone
	if err := readJSON(s.sub("removed", img.ID+".json"), &tomb); err != nil || tomb.By != "founder" || tomb.RemovedAt != now.Unix() {
		t.Fatalf("the tombstone says when and by whom: %+v %v", tomb, err)
	}

	text := "![a](attachment:" + img.ID + ")![e](attachment:" + expired.ID + ")"
	parts := s.Parts(text, false)
	if p := parts[0].(map[string]any); p["gone"] != true || p["removed"] != true {
		t.Fatalf("a removed image reads gone and removed: %v", p)
	}
	if p := parts[1].(map[string]any); p["gone"] != true || p["removed"] != nil {
		t.Fatalf("an expired image reads gone only: %v", p)
	}
	if f := s.FileMap(*img, true); f["gone"] != true || f["removed"] != true || f["path"] != nil {
		t.Fatalf("a removed memo file reads gone and removed, with no path: %v", f)
	}

	if n, already, err := s.Remove(q, img.ID, "founder", false); err != nil || !already || n != 0 {
		t.Fatalf("a second remove is already, 0: %d %v %v", n, already, err)
	}
	if _, _, err := s.Remove(q, expired.ID, "founder", false); err == nil {
		t.Fatal("an id no longer kept and never removed is not_found")
	}
}

// Each place adds one and a place that both names and carries the image counts once; request 10 and memo 10 share an id, so the
// message and memo keys must differ.
func TestNamingCountsEachPlaceOnce(t *testing.T) {
	_, _, q := testStore(t)
	id, other := "00000000000000aa", "00000000000000bb"
	ref := func(x string) string { return "see ![a](attachment:" + x + ")" }
	carried := []Info{{ID: id, Name: "a.png", Mime: "image/png", Bytes: 1}}
	for _, c := range []struct {
		about string
		write func() error
		want  int
	}{
		{"nothing names it", func() error { return nil }, 0},
		{"a message names it", func() error {
			_, err := q.Exec("INSERT INTO requests(id, requester, target, kind, state, ts, body) VALUES(10, 'human', 'w', 'say', 'landed', 1, ?)", ref(id))
			return err
		}, 1},
		{"the same message carries it too", func() error { return Record(q, 10, carried) }, 1},
		{"a message only carries it", func() error {
			if _, err := q.Exec("INSERT INTO requests(id, requester, target, kind, state, ts, body) VALUES(11, 'human', 'w', 'say', 'landed', 1, 'plain')"); err != nil {
				return err
			}
			return Record(q, 11, carried)
		}, 2},
		{"a message names another image", func() error {
			_, err := q.Exec("INSERT INTO requests(id, requester, target, kind, state, ts, body) VALUES(12, 'human', 'w', 'say', 'landed', 1, ?)", ref(other))
			return err
		}, 2},
		{"a memo with the message's id names it", func() error {
			if _, err := q.Exec("INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'w', 'boss', 1)"); err != nil {
				return err
			}
			_, err := q.Exec("INSERT INTO task_memos(id, task_id, author, body, created_at) VALUES(10, 1, 'w', ?, 1)", ref(id))
			return err
		}, 3},
		{"the same memo carries it as a file", func() error { return LinkMemo(q, 10, carried) }, 3},
		{"a memo only carries it", func() error {
			if _, err := q.Exec("INSERT INTO task_memos(id, task_id, author, body, created_at) VALUES(20, 1, 'w', 'plain', 1)"); err != nil {
				return err
			}
			return LinkMemo(q, 20, carried)
		}, 4},
		{"a send-back note names it", func() error {
			_, err := q.Exec("INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'boss', 'reject_task', json_object('task_id', 1, 'note', ?))", ref(id))
			return err
		}, 5},
		{"a handoff note names it", func() error {
			_, err := q.Exec("INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'boss', 'task_handoff', json_object('task_id', 1, 'note', ?))", ref(id))
			return err
		}, 6},
		{"another event naming it is no place", func() error {
			_, err := q.Exec("INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'boss', 'rpc_write', json_object('note', ?))", ref(id))
			return err
		}, 6},
	} {
		if err := c.write(); err != nil {
			t.Fatalf("%s: %v", c.about, err)
		}
		if n, err := Naming(q, id); err != nil || n != c.want {
			t.Fatalf("%s: %d %v, want %d", c.about, n, err, c.want)
		}
	}
}

func TestPlacesSaysOnePlaceInTheSingular(t *testing.T) {
	if places(1) != "1 place" || places(0) != "0 places" || places(2) != "2 places" {
		t.Fatal(places(0), places(1), places(2))
	}
}

type founderAuth struct{}

func (founderAuth) AgentOf(int) string         { return "" }
func (founderAuth) FounderKeyOK(k string) bool { return k == "founder" }
func (founderAuth) SessionsReadable() error    { return nil }

// A dry run records nothing and tells nobody; the remove records its write once and puts attachment.removed on chat and tasks.
func TestTheRemoveRecordsItsWriteAndADryRunNothing(t *testing.T) {
	s, _, q := testStore(t)
	img := putAll(t, s, q, "a.png", "image/png", pngOf(2, 2), 1000)
	srv := rpc.NewServer(founderAuth{}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	srv.DB = q
	var told []string
	Register(srv, s, func(topic, typ string, data map[string]any) error {
		told = append(told, topic+" "+typ+" "+fmt.Sprint(data["id"]))
		return nil
	})
	cl, sv := net.Pipe()
	done := make(chan struct{})
	go func() { srv.Serve(rpc.NewLineTransport(sv), 0); sv.Close(); close(done) }()
	defer func() { cl.Close(); <-done }()
	rd := bufio.NewReader(cl)
	cl.SetDeadline(time.Now().Add(10 * time.Second))
	call := func(id int, method, params string) map[string]any {
		cl.Write([]byte(fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":%q,"params":%s}`, id, method, params) + "\n"))
		b, err := rd.ReadBytes('\n')
		if err != nil {
			t.Fatal(err)
		}
		var m map[string]any
		json.Unmarshal(b, &m)
		return m
	}
	writes := func() (n int) {
		q.QueryRow("SELECT count(*) FROM events WHERE kind = 'rpc_write'").Scan(&n)
		return n
	}
	call(1, "hello", `{"client":"t","protocol":1,"key":"founder"}`)
	if m := call(2, "attachment.remove", `{"id":"`+img.ID+`","dry_run":"yes","request_id":"r1"}`); m["error"] == nil {
		t.Fatalf("dry_run that is not true or false is bad_request: %v", m)
	}
	if m := call(3, "attachment.remove", `{"id":"`+img.ID+`","dry_run":true,"request_id":"r2"}`); m["result"] == nil || writes() != 0 || len(told) != 0 {
		t.Fatalf("a dry run records and tells nothing: %v %d %v", m, writes(), told)
	}
	if m := call(4, "attachment.remove", `{"id":"`+img.ID+`","request_id":"r3"}`); m["result"] == nil || writes() != 1 {
		t.Fatalf("the remove records its write once: %v %d", m, writes())
	}
	if len(told) != 2 || told[0] != "chat attachment.removed "+img.ID || told[1] != "tasks attachment.removed "+img.ID {
		t.Fatalf("attachment.removed on chat and tasks: %v", told)
	}
}
