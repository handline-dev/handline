package claude

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/lifecycle"
)

// fixture is tests/fixtures/claude_driver.json: what the Python Claude driver writes (tests/support.py claude_driver_cases).
type fixture struct {
	Wanted []string `json:"wanted"`
	Launch []struct {
		Name        string   `json:"name"`
		Exe         *string  `json:"exe"`
		Model       *string  `json:"model"`
		Effort      *string  `json:"effort"`
		Disallowed  *string  `json:"disallowed"`
		Preamble    *string  `json:"preamble"`
		MemoryDir   *string  `json:"memory_dir"`
		MCP         []string `json:"mcp"`
		Hide        []string `json:"hide"`
		NoWrite     []string `json:"no_write"`
		Trusted     []string `json:"trusted"`
		MCPApproved []string `json:"mcp_approved"`
		ExtraDirs   []string `json:"extra_dirs"`
		Chrome      bool     `json:"chrome"`
		Sandbox     *bool    `json:"sandbox"`
		Argv        []string `json:"argv"`
	} `json:"launch"`
	Settings []struct {
		Name           string  `json:"name"`
		Before         *string `json:"before"`
		After          *string `json:"after"`
		EnsureRefused  bool    `json:"ensure_refused"`
		Missing        bool    `json:"missing"`
		StatusLineKept bool    `json:"statusline_kept"`
	} `json:"settings"`
	IsResume      map[string]bool `json:"is_resume"`
	TranscriptDir string          `json:"transcript_dir"`
}

func load(t *testing.T) fixture {
	b, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "fixtures", "claude_driver.json"))
	if err != nil {
		t.Fatal(err)
	}
	var f fixture
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	return f
}

func str(p *string, def string) string {
	if p == nil {
		return def
	}
	return *p
}

func TestLaunchArgvIsWhatPythonWrites(t *testing.T) {
	f := load(t)
	if len(f.Launch) < 8 {
		t.Fatalf("only %d cases", len(f.Launch))
	}
	for _, c := range f.Launch {
		o := lifecycle.LaunchArgs{Model: str(c.Model, ""), Effort: str(c.Effort, "high"), Disallowed: str(c.Disallowed, "Task"),
			MCPArgs: c.MCP, Preamble: str(c.Preamble, ""), Hide: c.Hide, NoWrite: c.NoWrite, MemoryDir: str(c.MemoryDir, ""),
			Trusted: c.Trusted, Chrome: c.Chrome, SandboxOff: c.Sandbox != nil && !*c.Sandbox, MCPApproved: c.MCPApproved,
			ExtraDirs: c.ExtraDirs}
		if got := (Driver{}).LaunchArgv(str(c.Exe, "/bin/claude"), o); !reflect.DeepEqual(got, c.Argv) {
			t.Errorf("%s:\n got  %q\n want %q", c.Name, got, c.Argv)
		}
	}
}

func TestTheSmallMethodsAreWhatPythonSays(t *testing.T) {
	f := load(t)
	want := slices.Clone(Wanted)
	slices.Sort(want)
	if !reflect.DeepEqual(want, f.Wanted) {
		t.Errorf("wanted events %v, Python's %v", want, f.Wanted)
	}
	for line, resume := range f.IsResume {
		if got := (Driver{}).IsResume(strings.Fields(line)); got != resume {
			t.Errorf("IsResume(%q) = %v", line, got)
		}
	}
	if got, _ := (Driver{}).TranscriptDir("/c", "/Users/user/my repo.v2"); got != f.TranscriptDir {
		t.Errorf("TranscriptDir %q, Python's %q", got, f.TranscriptDir)
	}
}

func TestKeepingTheSharedSettingsCurrentWritesWhatPythonWrites(t *testing.T) {
	f := load(t)
	root := t.TempDir()
	for _, c := range f.Settings {
		t.Run(c.Name, func(t *testing.T) {
			home := filepath.Join(root, c.Name)
			real, err := filepath.EvalSymlinks(root)
			if err != nil {
				t.Fatal(err)
			}
			real = filepath.Join(real, c.Name)
			h := Hooks{Home: home}
			if c.Before != nil {
				if err := os.MkdirAll(filepath.Dir(h.Target()), 0o755); err != nil {
					t.Fatal(err)
				}
				text := strings.NewReplacer("{home}", home, "{realhome}", real).Replace(*c.Before)
				if err := os.WriteFile(h.Target(), []byte(text), 0o644); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(filepath.Dir(h.Target()), "statusline.sh"), []byte("old"), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			// What a launch does to keep them current (lifecycle.Launcher.Build).
			if err := h.EnsureShared(); (err != nil) != c.EnsureRefused {
				t.Fatalf("EnsureShared: %v, Python refused: %v", err, c.EnsureRefused)
			}
			if !c.EnsureRefused {
				if m := h.Missing(); m != c.Missing {
					t.Errorf("Missing %v, Python's %v", m, c.Missing)
				} else if m {
					if err := h.Install("/opt/bin/hl"); err != nil {
						t.Fatal(err)
					}
				}
				if err := h.AllowHomeSocket(); err != nil {
					t.Fatal(err)
				}
			}
			var after *string
			if b, err := os.ReadFile(h.Target()); err == nil {
				s := strings.ReplaceAll(strings.ReplaceAll(string(b), real, "{realhome}"), home, "{home}")
				after = &s
			}
			if str(after, "<none>") != str(c.After, "<none>") {
				t.Errorf("settings\n got  %s\n want %s", str(after, "<none>"), str(c.After, "<none>"))
			}
			_, err = os.Stat(filepath.Join(filepath.Dir(h.Target()), "statusline.sh"))
			if (err == nil) != c.StatusLineKept {
				t.Errorf("statusline.sh kept: %v, Python's %v", err == nil, c.StatusLineKept)
			}
			// A second launch changes nothing.
			if !c.EnsureRefused {
				before := str(after, "")
				_ = h.EnsureShared()
				if h.Missing() {
					t.Error("still missing after Install")
				}
				_ = h.AllowHomeSocket()
				if b, _ := os.ReadFile(h.Target()); strings.ReplaceAll(strings.ReplaceAll(string(b), real, "{realhome}"), home, "{home}") != before {
					t.Error("a second launch changed the settings")
				}
			}
		})
	}
}
