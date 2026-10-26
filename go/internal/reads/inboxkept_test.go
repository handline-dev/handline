package reads

import (
	"testing"
	"time"
)

// A queued ask with read_at set stays unread. A say the founder has read stays in the list for 7 days, as read; the counts (FounderItems) never include it.
func TestReadSaysStayInTheInboxListForSevenDays(t *testing.T) {
	e := testEnv(t)
	now := time.Now().Unix()
	run(t, e, "INSERT INTO requests(id, ref, ts, requester, target, body, state, kind, edge_ok, landed_at, read_at) VALUES"+
		"(1, 'aaaaaaaaaaa1', 10, 'bob', 'human', 'fresh read', 'landed', 'say', 1, 10, "+itoa(now-3600)+"), "+
		"(2, 'aaaaaaaaaaa2', 20, 'bob', 'human', 'old read', 'landed', 'say', 1, 20, "+itoa(now-8*24*3600)+"), "+
		"(3, 'aaaaaaaaaaa3', 30, 'bob', 'human', 'unread', 'landed', 'say', 1, 30, NULL), "+
		"(4, 'aaaaaaaaaaa4', 40, 'bob', 'human', 'queued ask marked read', 'queued', 'ask', 1, NULL, "+itoa(now-60)+")")
	listed, err := e.founderItemsKept(0, true)
	if err != nil {
		t.Fatal(err)
	}
	read := map[int64]bool{}
	for _, it := range listed {
		read[it["id"].(int64)] = it["read"].(bool)
	}
	if len(read) != 3 || !read[1] || read[3] || read[4] {
		t.Fatalf("want the fresh read say (read) and the unread one (unread), not the 8-day-old: %v", read)
	}
	counted, err := e.FounderItems(0)
	if err != nil {
		t.Fatal(err)
	}
	if len(counted) != 2 || counted[0]["id"].(int64) != 3 || counted[1]["read"].(bool) {
		t.Fatalf("a read say is not counted: %v", counted)
	}
}
