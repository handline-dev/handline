package lifecycle

import (
	"os"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
)

func settingsWith(t *testing.T, config, agent string) *Settings {
	t.Helper()
	home := t.TempDir()
	s := &Settings{Home: home, Roster: team.NewRoster(home)}
	if config != "" {
		if err := os.WriteFile(paths.ConfigFile(home), []byte(config), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if agent != "" {
		_ = os.MkdirAll(strings.TrimSuffix(s.Roster.AgentFile("ann"), "agent.yaml"), 0o755)
		if err := os.WriteFile(s.Roster.AgentFile("ann"), []byte(agent), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return s
}

func TestSettingsReadTheAgentsOwnThenTheGlobalThenTheDefault(t *testing.T) {
	s := settingsWith(t, "bg_notice_s: 100\nbg_check_s: 0\nnotice_digest_s: 40\npark_timeout_s: 7\n", "vendor: claude\nbg_grace_s: 9\npark_timeout_s: 0\n")
	if s.BgGraceS("ann") != 9 || s.BgGraceS("bob") != BgGraceS {
		t.Fatal("grace")
	}
	if s.BgNoticeS("ann") != 100 || s.NoticeDigestS("ann") != 40 {
		t.Fatal("global")
	}
	if s.BgCheckS("ann") != BgCheckS {
		t.Fatal("a zero is not a positive number of seconds")
	}
	if s.CloseTimeoutS("ann") != 0 || s.CloseTimeoutS("bob") != 7 || s.Never() != Never {
		t.Fatal("park timeout: the agent's zero means never")
	}
	if got := s.ConfigSeconds("bg_notice_s", 5); got != 100 {
		t.Fatal(got)
	}
	if got := s.ConfigSeconds("missing", 5); got != 5 {
		t.Fatal(got)
	}
	// A config nobody can read is empty, not a failure.
	if bad := settingsWith(t, "{{{", "vendor: [unclosed\n"); bad.BgGraceS("ann") != BgGraceS {
		t.Fatal("unreadable config")
	}
}

func TestSetFlagKeepsTheOtherSettingsAndRefusesToOverwriteWhatIsNotAMapping(t *testing.T) {
	s := settingsWith(t, "park_timeout_s: 7\nignore_cache_expiry: false\n", "")
	if s.Flag("ignore_cache_expiry", true) {
		t.Fatal("read false as true")
	}
	if !s.Flag("absent", true) {
		t.Fatal("default lost")
	}
	if err := s.SetFlag("ignore_cache_expiry", true); err != nil || !s.Flag("ignore_cache_expiry", false) {
		t.Fatal(err)
	}
	if err := s.SetFlag("new_flag", true); err != nil || s.DefaultCloseTimeoutS() != 7 || !s.Flag("new_flag", false) {
		t.Fatal(err)
	}
	list := settingsWith(t, "- a\n- b\n", "")
	if err := list.SetFlag("x", true); err == nil {
		t.Fatal("a list was overwritten")
	}
	if b, _ := os.ReadFile(paths.ConfigFile(list.Home)); string(b) != "- a\n- b\n" {
		t.Fatalf("%q", b)
	}
	if fresh := settingsWith(t, "", ""); fresh.SetFlag("x", true) != nil || !fresh.Flag("x", false) {
		t.Fatal("no config.yaml yet")
	}
}
