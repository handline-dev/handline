package chatwrites

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	dbpkg "github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/worktreeprune"
)

// askedAbout is the founder's open task about a worktree with one untracked file, as retire raises it.
func askedAbout(t *testing.T, r *rig) (tid int64, path string, main string) {
	t.Helper()
	root, _ := filepath.EvalSymlinks(t.TempDir())
	main = filepath.Join(root, "main")
	path = filepath.Join(root, "bobs")
	os.MkdirAll(main, 0o755)
	for _, c := range [][]string{{main, "init", "-q", "-b", "main"}, {main, "commit", "-q", "--allow-empty", "-m", "start"}, {main, "worktree", "add", "-q", "-b", "bobs", path}} {
		if out := gitrun.Run(c[0], append([]string{"-c", "user.name=t", "-c", "user.email=t@t"}, c[1:]...)...); out.RC != 0 {
			t.Fatalf("git %v: %s", c[1:], out.Stderr)
		}
	}
	os.WriteFile(filepath.Join(path, "left.txt"), []byte("work"), 0o644)
	card := r.request("human", "human", "Remove bob's worktree?\n\nPath: "+path+"\nBranch: bobs\n\nMember: bob\n\nReply with the number")
	tid, err := dbpkg.NewTask(r.db, "Remove bob's worktree?", "human", "human", 0, dbpkg.DefaultPriority, "decision", 0, "")
	if err != nil {
		t.Fatal(err)
	}
	dbpkg.LinkRequest(r.db, tid, card)
	r.db.Exec("INSERT INTO task_tags(task_id, tag) VALUES(?, ?)", tid, worktreeprune.Tag)
	return tid, path, main
}

func answer(t *testing.T, r *rig, tid int64, words string) (bool, string, error) {
	t.Helper()
	tx, err := r.db.Begin()
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	return answerWorktree(tx, tid, words, time.Date(2026, 10, 26, 0, 0, 0, 0, time.UTC))
}

func TestAnswerOneKeepsACopyThenRemovesTheWorktree(t *testing.T) {
	r := newRig(t)
	tid, path, main := askedAbout(t, r)
	handled, said, err := answer(t, r, tid, "1")
	if !handled || err != nil || !strings.Contains(said, "rescue/bob-2026-10-26") {
		t.Fatalf("%v %q %v", handled, said, err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("the worktree is still there")
	}
	if out := gitrun.Run(main, "show", "rescue/bob-2026-10-26:left.txt"); out.RC != 0 || out.Stdout != "work" {
		t.Fatalf("the file is not on the rescue branch: %+v", out)
	}
}

func TestAnswerTwoNeedsYesAndThreeLeavesIt(t *testing.T) {
	r := newRig(t)
	tid, path, _ := askedAbout(t, r)
	if handled, _, err := answer(t, r, tid, "2"); !handled || err == nil || !strings.Contains(err.Error(), "2 yes") {
		t.Fatalf("a bare 2 asks again: %v %v", handled, err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatal("a bare 2 removed it")
	}
	for _, words := range []string{"", "maybe", "4"} {
		if handled, _, err := answer(t, r, tid, words); !handled || err == nil {
			t.Fatalf("%q: %v %v", words, handled, err)
		}
	}
	if handled, said, err := answer(t, r, tid, "3"); !handled || err != nil || !strings.Contains(said, "stays") {
		t.Fatalf("%v %q %v", handled, said, err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatal("3 removed it")
	}
	if handled, _, err := answer(t, r, tid, "2 yes"); !handled || err != nil {
		t.Fatalf("%v %v", handled, err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("2 yes left it")
	}
}

func TestAnswerIsNotHandledForAnyOtherTask(t *testing.T) {
	r := newRig(t)
	card := r.request("alice", "human", "which one?")
	tid, _ := dbpkg.NewTask(r.db, "which one?", "human", "alice", 0, dbpkg.DefaultPriority, "decision", 0, "")
	dbpkg.LinkRequest(r.db, tid, card)
	if handled, _, err := answer(t, r, tid, "1"); handled || err != nil {
		t.Fatal(handled, err)
	}
}

func TestADecisionActsOnTheAnswerThenClosesTheTaskWithWhatWasDone(t *testing.T) {
	r := newRig(t)
	tid, path, _ := askedAbout(t, r)
	tx, err := r.db.Begin()
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	res, err := decide(r.env, r.env, tx, tid, "2", nil)
	if err != nil || res.OK || !strings.Contains(res.Msg, "2 yes") {
		t.Fatalf("a bare 2 is refused and the task stays open: %+v %v", res, err)
	}
	res, err = decide(r.env, r.env, tx, tid, "3", nil)
	if err != nil || !res.OK {
		t.Fatalf("%+v %v", res, err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	res.Tell()
	if r.count("SELECT COUNT(*) FROM tasks WHERE id = ? AND done_evidence = ?", tid, "3: "+path+" stays") != 1 {
		t.Fatal("the task closes with what was done")
	}
}

// Tags are free text: an agent's task that carries the tag and the lines is no retire question, and the founder's answer to it removes nothing.
func TestATaggedTaskAnAgentOpenedIsNotARetireQuestion(t *testing.T) {
	r := newRig(t)
	tid, path, _ := askedAbout(t, r)
	r.db.Exec("UPDATE tasks SET opened_by = 'alice' WHERE id = ?", tid)
	if handled, _, err := answer(t, r, tid, "2 yes"); handled || err != nil {
		t.Fatalf("%v %v", handled, err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatal("an agent's task removed a worktree")
	}
}

func TestOnlyARegisteredLinkedWorktreeIsActedOn(t *testing.T) {
	r := newRig(t)
	tid, _, main := askedAbout(t, r)
	r.db.Exec("UPDATE requests SET body = ? WHERE id = (SELECT MIN(request_id) FROM task_requests WHERE task_id = ?)", "Path: "+main+"\nMember: bob", tid)
	if handled, _, err := answer(t, r, tid, "2 yes"); !handled || err == nil || !strings.Contains(err.Error(), "not a worktree git knows") {
		t.Fatalf("the main checkout: %v %v", handled, err)
	}
	if _, err := os.Stat(main); err != nil {
		t.Fatal("the main checkout was touched")
	}
}
