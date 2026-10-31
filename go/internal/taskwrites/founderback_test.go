package taskwrites

import (
	"fmt"
	"testing"
)

func TestFounderTasksGoBackToWhoHandedThemOver(t *testing.T) {
	home := t.TempDir()
	sdb, f, _ := reportEnvIn(t, home,
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 'handed', 'human', 'boss', 1), (2, 'opened', 'human', 'w', 1), "+
			"(3, 'ask', 'human', 'human', 1), (4, 'notice', 'human', 'task-board', 1), (5, 'done', 'human', 'boss', 1)",
		"UPDATE tasks SET done_evidence = 'ok' WHERE id = 5",
		`INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'w', 'task_handoff', '{"task_id":1,"from":"w","to":"human","note":"which one?"}'), `+
			`(2, 'gate', 'task_handoff', '{"task_id":1,"from":"gate","to":"human","note":"later"}')`,
	)
	said, err := ReturnFounderTasks(sdb, home, f)
	if err != nil || fmt.Sprint(said) != "[T1 goes back to w T2 goes back to w]" {
		t.Fatalf("%q %v", said, err)
	}
	for id, want := range map[int]int{1: 1, 2: 1, 3: 0, 4: 0, 5: 0} {
		if got := count(t, sdb, fmt.Sprintf("SELECT COUNT(*) FROM tasks WHERE id = %d AND owner = 'w'", id)); got != want {
			t.Errorf("T%d: owner w is %d, want %d", id, got, want)
		}
	}
	if count(t, sdb, "SELECT COUNT(*) FROM task_memos WHERE task_id = 1 AND author = 'w' AND body = '@human which one?'") != 1 ||
		count(t, sdb, "SELECT COUNT(*) FROM requests WHERE requester = 'w' AND target = 'human' AND state = 'queued'") != 1 || len(f.handed) != 2 {
		t.Errorf("each goes back with an ask to the founder, and its owner is told: %+v", f.handed)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM task_memos WHERE task_id = 2 AND author = 'daemon' AND body = '@human opened'") != 1 ||
		count(t, sdb, "SELECT COUNT(*) FROM requests WHERE requester = 'daemon' AND target = 'human' AND body = '@human opened'") != 1 {
		t.Errorf("a task handed over with no note asks with its title, in the daemon's name")
	}
	sdb.Exec("UPDATE tasks SET owner = 'human' WHERE id = 2") // the founder may take a task, and it stays theirs after a restart
	if again, err := ReturnFounderTasks(sdb, home, f); err != nil || len(again) != 0 {
		t.Fatalf("it runs once: %q %v", again, err)
	}
	if count(t, sdb, "SELECT COUNT(*) FROM tasks WHERE id = 2 AND owner = 'human'") != 1 {
		t.Fatal("a task the founder took stays theirs")
	}
}
