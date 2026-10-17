package taskwrites

import (
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/attachments"
)

// onePNG is a 1x1 PNG.
var onePNG, _ = base64.StdEncoding.DecodeString("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

// imagesEnv is reportEnv with T1 (a brief by boss, delivered to w) and T2 (w's, no brief), and the daemon's attachments store.
func imagesEnv(t *testing.T) (*sql.DB, *attachments.Store, func(agent, method, rid, params string) map[string]any) {
	t.Helper()
	home := t.TempDir()
	sdb, _, run := reportEnvIn(t, home,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at, sent_at) VALUES(1, 'a', 'w', 'boss', 1, 1)",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body, landed_at, delivered_at) VALUES(10, 'boss', 'w', 'task', 'landed', 1, 'a', 1, 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10)",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(2, 'b', 'w', 'boss', 1)",
	)
	return sdb, attachments.New(home), run
}

// putImage is a finished upload, as a member's attachment.put leaves it.
func putImage(t *testing.T, sdb *sql.DB, s *attachments.Store) string {
	t.Helper()
	out, err := s.Put(sdb, attachments.PutIn{Name: "shot.png", Mime: "image/png", Bytes: int64(len(onePNG)), Data: onePNG})
	if err != nil || out.Attachment == nil {
		t.Fatalf("put: %v %v", out, err)
	}
	return out.Attachment.ID
}

func errData(r map[string]any) map[string]any {
	e, _ := r["error"].(map[string]any)
	d, _ := e["data"].(map[string]any)
	return d
}

func ref(id string) string { return fmt.Sprintf(`![the button](attachment:%s)`, id) }

func params(task int, key, text string) string {
	return fmt.Sprintf(`,"task":%d,%q:%q`, task, key, text)
}

func TestATaskTextNamesOnlyFinishedImagesThatAreStillKept(t *testing.T) {
	sdb, s, run := imagesEnv(t)
	img := putImage(t, sdb, s)
	unfinished, err := s.Put(sdb, attachments.PutIn{Name: "a.png", Mime: "image/png", Bytes: 1000, Data: onePNG[:8]})
	if err != nil {
		t.Fatal(err)
	}
	seven := ""
	for range 7 {
		seven += ref(img)
	}
	for i, c := range []struct{ text, code, field, what string }{
		{"see " + ref("0000000000000000"), "not_found", "", "attachment"},
		{"see " + ref("not-an-id"), "bad_request", "text", ""},
		{"see " + ref(unfinished.Upload), "bad_request", "text", ""},
		{seven, "bad_request", "text", ""},
	} {
		r := run("", "task.memo.add", fmt.Sprint("m", i), params(1, "text", c.text))
		if d := errData(r); d["code"] != c.code || d["field"] != nil && d["field"] != c.field || c.what != "" && d["what"] != c.what {
			t.Fatalf("%q: %v", c.text, r)
		}
	}
	said(t, run("", "task.memo.add", "ok", params(1, "text", "cut off "+ref(img))))
	if r := run("", "task.new", "n1", `,"owner":"human","title":"x","body":"see `+ref("0000000000000000")+`"`); errData(r)["what"] != "attachment" {
		t.Fatalf("task.new checks its body: %v", r)
	}
}

func TestAnAgentNamesOnlyAnImageATaskAlreadyShows(t *testing.T) {
	sdb, s, run := imagesEnv(t)
	img := putImage(t, sdb, s)
	r := run("w", "task.memo.add", "m1", params(2, "text", ref(img)))
	if d := errData(r); d["code"] != "refused" || d["gate"] != "role" {
		t.Fatalf("an image no task shows: %v", r)
	}
	if _, err := sdb.Exec("UPDATE requests SET body = ? WHERE id = 10", "a, as in "+ref(img)); err != nil { // the founder's brief of T1
		t.Fatal(err)
	}
	said(t, run("w", "task.memo.add", "m2", params(2, "text", "carried from T1: "+ref(img))))
	if count(t, sdb, "SELECT COUNT(*) FROM task_memos WHERE task_id = 2 AND body = 'carried from T1: "+ref(img)+"'") != 1 {
		t.Fatal("the memo is stored as written")
	}
}

func TestAnEditThatKeepsAReferenceToAGoneImageIsNotRefusedForIt(t *testing.T) {
	sdb, s, run := imagesEnv(t)
	img := putImage(t, sdb, s)
	said(t, run("", "task.memo.add", "m1", params(1, "text", "one "+ref(img))))
	said(t, run("boss", "task.edit", "e1", params(1, "body", "a "+ref(img))))
	info, _ := s.Lookup(img)
	os.Remove(s.Path(info))
	said(t, run("", "task.memo.edit", "m2", `,"memo":1`+params(1, "text", "two "+ref(img))))
	said(t, run("boss", "task.edit", "e2", params(1, "body", "b "+ref(img))))
	if r := run("", "task.memo.add", "m3", params(1, "text", "three "+ref(img))); errData(r)["code"] != "not_found" {
		t.Fatalf("a new text naming it is refused: %v", r)
	}
}

func TestARejectNoteIsOneLineThenItsImages(t *testing.T) {
	sdb, s, run := imagesEnv(t)
	img := putImage(t, sdb, s)
	said(t, run("w", "task.done", "d1", `,"task":1,"evidence":"half"`))
	if r := run("", "task.reject", "r1", params(1, "note", "cut off "+ref(img)+" and more words")); errData(r)["field"] != "note" {
		t.Fatalf("words after the images: %v", r)
	}
	said(t, run("", "task.reject", "r2", params(1, "note", "cut off\non a small window "+ref(img)+"\n")))
	want := "cut off on a small window\n" + ref(img)
	var n int
	if err := sdb.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'reject_task' AND json_extract(payload, '$.note') = ?", want).Scan(&n); err != nil || n != 1 {
		t.Fatal("the note is kept as one line, then its image")
	}
}

func TestATaskWriteClaimsTheImagesItNames(t *testing.T) {
	sdb, s, run := imagesEnv(t)
	img := putImage(t, sdb, s)
	claimed := func() any {
		var m map[string]any
		b, _ := os.ReadFile(filepath.Join(s.Dir, "meta", img+".json"))
		json.Unmarshal(b, &m)
		return m["claimed"]
	}
	if claimed() != nil {
		t.Fatal("a fresh upload is claimed by nothing")
	}
	said(t, run("", "task.memo.add", "m1", params(1, "text", "see "+ref(img))))
	if claimed() == nil {
		t.Fatal("the memo claims the image it names")
	}
}

func TestAHandoffNoteNamesImagesAsAMemoDoes(t *testing.T) {
	sdb, s, run := imagesEnv(t)
	img := putImage(t, sdb, s)
	if r := run("w", "task.handoff", "h1", `,"task":1,"to":"human","note":"see `+ref(img)+`"`); errData(r)["gate"] != "role" {
		t.Fatalf("an agent's note names only an image a task or message shows: %v", r)
	}
	said(t, run("", "task.memo.add", "m1", params(1, "text", "the founder's "+ref(img))))
	said(t, run("w", "task.handoff", "h2", `,"task":1,"to":"human","note":"see `+ref(img)+`"`))
	sdb.Exec("DELETE FROM task_memos")
	if held, _ := attachments.Held(sdb, 5); held[img] != 5 {
		t.Fatalf("the note holds the image while the task is open: %v", held)
	}
}

func TestAnAgentAttachesToAMemoOnlyFilesFromItsOwnPlaces(t *testing.T) {
	home := t.TempDir()
	_, _, run := reportEnvIn(t, home, "INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'a', 'w', 'boss', 1)")
	outside := filepath.Join(t.TempDir(), "plan.md")
	inside := filepath.Join(home, "agents", "w", "plan.md")
	for _, p := range []string{outside, inside} {
		os.WriteFile(p, []byte("# plan"), 0o644)
	}
	files := func(p string) string { b, _ := json.Marshal([]string{p}); return `,"files":` + string(b) }
	if r := run("w", "task.memo.add", "m1", params(1, "text", "see")+files(outside)); errData(r)["field"] != "files" {
		t.Fatalf("a file outside the agent's places was read: %v", r)
	}
	said(t, run("w", "task.memo.add", "m2", params(1, "text", "see")+files(inside)))
}
