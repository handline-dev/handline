package compose

import (
	"os"
	"testing"
	"time"
)

func TestPidStartReadsTheProcessStart(t *testing.T) {
	got := (termRun{}).PidStart(os.Getpid())
	if got == 0 || got > time.Now().Unix() || time.Now().Unix()-got > 3600 {
		t.Fatalf("start %d", got)
	}
	if (termRun{}).PidStart(1<<30) != 0 {
		t.Fatal("a pid that is not there has a start")
	}
}
