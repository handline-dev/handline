package reads

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/sealed"
	"github.com/handline/handline/go/internal/team"
)

func TestScheduleValueErrors(t *testing.T) {
	e := &Env{Home: t.TempDir()}
	dir := filepath.Join(e.Home, "agents", "a")
	os.MkdirAll(dir, 0o755)
	for _, c := range []struct{ value, want string }{
		{"not json", "schedules is not JSON: Expecting value: line 1 column 1 (char 0)"},
		{`{"cron": "* * * * *"}`, "schedules is a list of {cron, message}"},
		{`[{"cron": "* * * * *", "message": "m"}, {"cron": "* * * * *"}]`, "schedule 2 is {cron, message} and nothing else"},
		{`[{"cron": "x * * * *", "message": "m"}]`, "minute: 'x' is not a number, a range or *"},
		{`[{"cron": "60 * * * *", "message": "m"}]`, "minute: '60' is outside 0-59"},
		{`[{"cron": "*/0 * * * *", "message": "m"}]`, "minute: a step is a positive number, not '0'"},
		{`[{"cron": "* * * *", "message": "m"}]`, "a cron expression has five fields: minute hour day-of-month month day-of-week"},
		{`[{"cron": "* * * * *", "message": "  "}]`, "schedule 1 has no message"},
	} {
		text := "role: x\nschedules: '" + c.value + "'\n"
		os.WriteFile(filepath.Join(dir, "agent.yaml"), []byte(text), 0o644)
		team.NewRoster(e.Home).Seals.Seal(sealed.AgentKey("a"), text)
		_, err := e.loadSchedules("a")
		if bv, ok := err.(badValue); !ok || string(bv) != c.want {
			t.Errorf("%s: got %v, want %q", c.value, err, c.want)
		}
	}
}
