package db

import "testing"

func TestAMessageIsNamedByTheShortestPrefixOfItsRefThatNoOtherBegins(t *testing.T) {
	q := open(t)
	ids := map[string]int64{}
	for _, ref := range []string{"3f9a0c71be2d", "3f9a0c71ffff", "aaaaaaaaaaaa"} {
		rid, err := AddRequest(q, NewRequest{Requester: "a", Target: "b", Body: "x"})
		if err != nil {
			t.Fatal(err)
		}
		if _, err := q.Exec("UPDATE requests SET ref = ? WHERE id = ?", ref, rid); err != nil {
			t.Fatal(err)
		}
		ids[ref] = rid
	}
	for ref, want := range map[string]string{"3f9a0c71be2d": "msg_3f9a0c71b", "3f9a0c71ffff": "msg_3f9a0c71f", "aaaaaaaaaaaa": "msg_aaaaaaa"} { // local-ref-ok: names are the feature under test
		if got := MsgName(q, ids[ref]); got != want {
			t.Errorf("%s: %s, want %s", ref, got, want)
		}
	}
	if got := MsgName(q, 999); got != "an unknown message" {
		t.Fatal(got)
	}
}

func TestAMessageIsFoundInTextByItsNameOrAStoredHash(t *testing.T) {
	q := open(t)
	rid, _ := AddRequest(q, NewRequest{Requester: "a", Target: "b", Body: "x"})
	q.Exec("UPDATE requests SET ref = '3f9a0c71be2d' WHERE id = ?", rid)
	for text, want := range map[string]string{
		"see msg_beef123":        "msg_beef123", // local-ref-ok: names are the feature under test
		"as 3f9a0c7 said":        "3f9a0c7",
		"3F9A0C71BE2D":           "3F9A0C71BE2D",
		"commit 1234567 landed":  "",
		"sha 3f9a0c71be2d00aa11": "",
		"T42 and #7":             "", // local-ref-ok: names are the feature under test
	} {
		if got := MessageNamedIn(q, text); got != want {
			t.Errorf("%q: %q, want %q", text, got, want)
		}
	}
}
