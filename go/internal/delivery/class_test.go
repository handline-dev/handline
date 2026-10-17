package delivery

import "testing"

func TestFailedReadsFailureAndBlockedWords(t *testing.T) {
	for in, want := range map[string]bool{"FAIL: tests": true, "failed to build": true, "Blocked on x": true, "fatal error": true, "FAILING": false,
		"done": false, "not a failure": false, "": false} {
		if Failed(in) != want {
			t.Errorf("%q", in)
		}
	}
}

func TestOnlyWhatIsAimedOrHasFailedIsTypedAtOnce(t *testing.T) {
	for _, c := range []struct {
		kind          Kind
		aimed, failed bool
		want          Class
	}{
		{Result, false, false, ClassBatch}, {Result, true, false, ClassNow}, {Result, false, true, ClassNow},
		{TaskChange, false, false, ClassBatch}, {TaskChange, false, true, ClassBatch}, {Memo, false, false, ClassBatch}, {Memo, true, false, ClassNow},
	} {
		if got := ClassOf(c.kind, c.aimed, c.failed); got != c.want {
			t.Errorf("%+v: %v", c, got)
		}
	}
}
