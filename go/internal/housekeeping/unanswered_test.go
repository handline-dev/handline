package housekeeping

import (
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/store"
)

func TestALeadIsToldOnceOfAFounderMessageWithNoTask(t *testing.T) {
	d, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	now := int64(1_000_000)
	old, fresh := now-ChaseAfterS-1, now-60
	for _, q := range []string{
		"INSERT INTO requests(id, ref, requester, target, body, state, kind, ts) VALUES" +
			"(1, 'aaaaaaaaaaa1', 'human', 'lead', 'plain old message\nsecond line', 'landed', 'say', " + itoa(old) + "), " +
			"(2, 'aaaaaaaaaaa2', 'human', 'lead', 'fresh', 'landed', 'say', " + itoa(fresh) + "), " +
			"(3, 'aaaaaaaaaaa3', 'human', 'lead', 'has a task', 'landed', 'say', " + itoa(old) + "), " +
			"(4, 'aaaaaaaaaaa4', 'human', 'lead', 'marked', 'landed', 'say', " + itoa(old) + "), " +
			"(5, 'aaaaaaaaaaa5', 'human', 'member', 'to a member', 'landed', 'say', " + itoa(old) + "), " +
			"(6, 'aaaaaaaaaaa6', 'human', 'lead', 'a reply in a thread', 'landed', 'say', " + itoa(old) + "), " +
			"(7, 'aaaaaaaaaaa7', 'human', 'lead', 'long ago', 'landed', 'say', " + itoa(now-chaseWindowS-5) + ")",
		"UPDATE requests SET parent_id = 1 WHERE id = 6",
		"UPDATE requests SET no_work = 'thanks' WHERE id = 4",
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'lead', 'human', 1)",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 3)",
	} {
		if _, err := d.Exec(q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	var told []string
	u := &Unanswered{Lead: func(a string) bool { return a == "lead" }, Log: func(string) {},
		Notice: func(_ db.Q, to, text, _ string) error { told = append(told, to+": "+text); return nil }}
	for i := 0; i < 2; i++ {
		if err := u.Check(d, now); err != nil {
			t.Fatal(err)
		}
	}
	if len(told) != 1 || !strings.HasPrefix(told[0], "lead: The founder's message ") || !strings.Contains(told[0], `"plain old message"`) {
		t.Fatalf("one notice, once: %q", told)
	}
}

func itoa(n int64) string { return strconv.FormatInt(n, 10) }
