package service

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

// rig is a scratch home with a fake launchd; each daemon it builds is a script that prints its version, and answers the probe
// with it unless it is broken.
type rig struct {
	t      *testing.T
	s      *Service
	root   string
	loaded bool
	calls  []string
}

func newRig(t *testing.T) *rig {
	root := t.TempDir()
	tools := filepath.Join(root, "tools")
	os.MkdirAll(tools, 0o755)
	os.WriteFile(filepath.Join(tools, "claude"), []byte("#!/bin/sh\n"), 0o755)
	t.Setenv("PATH", tools+":"+os.Getenv("PATH"))
	r := &rig{t: t, root: root}
	r.s = &Service{
		Home: filepath.Join(root, "home"), Label: "test.handline.service", Bin: filepath.Join(root, "bin"),
		Plist: filepath.Join(root, "LaunchAgents", "test.handline.service.plist"), Wait: 300 * time.Millisecond,
		Version: builtVersion,
		Launchctl: func(args ...string) (string, error) {
			r.calls = append(r.calls, args[0])
			switch args[0] {
			case "print":
				if !r.loaded {
					return "not found", errors.New("exit 113")
				}
			case "bootstrap":
				r.loaded = true
			case "bootout":
				r.loaded = false
			}
			return "", nil
		},
		Probe: func() (string, error) {
			out, err := exec.Command(filepath.Join(r.s.Bin, current, "handlined"), "-health").Output()
			return strings.TrimSpace(string(out)), err
		},
	}
	return r
}

// build is a folder holding handlined and hl at version v.
func (r *rig) build(v string, broken bool) string {
	dir := filepath.Join(r.root, "build-"+v)
	os.MkdirAll(dir, 0o755)
	health := "echo " + v
	if broken {
		health = "exit 1"
	}
	script := "#!/bin/sh\nif [ \"$1\" = -version ]; then echo " + v + "; exit 0; fi\n" + health + "\n"
	os.WriteFile(filepath.Join(dir, "handlined"), []byte(script), 0o755)
	os.WriteFile(filepath.Join(dir, "hl"), []byte("#!/bin/sh\n"), 0o755)
	return dir
}

func (r *rig) want(cur string, kept ...string) {
	r.t.Helper()
	got := r.s.kept()
	slices.Sort(got)
	slices.Sort(kept)
	if r.s.Current() != cur || !slices.Equal(got, kept) {
		r.t.Fatalf("current %q kept %v, want %q %v", r.s.Current(), got, cur, kept)
	}
}

func TestABrokenUpgradeRollsBackToTheVersionThatRan(t *testing.T) {
	r := newRig(t)
	if msg, err := r.s.Install(r.build("v1", false), false); err != nil {
		t.Fatal(err)
	} else if !strings.Contains(msg, "v1") {
		t.Error(msg)
	}
	r.want("v1", "v1")
	plist, _ := os.ReadFile(r.s.Plist)
	if !strings.Contains(string(plist), filepath.Join(r.s.Bin, "current", "handlined")) || !strings.Contains(string(plist), "-home") || !r.loaded {
		t.Fatalf("job: loaded %v\n%s", r.loaded, plist)
	}
	if _, err := r.s.Upgrade(r.build("v2", false)); err != nil {
		t.Fatal(err)
	}
	r.want("v2", "v1", "v2")

	_, err := r.s.Upgrade(r.build("v3", true))
	if err == nil || !strings.Contains(err.Error(), "rolled back to v2") {
		t.Fatalf("broken upgrade: %v", err)
	}
	r.want("v2", "v1", "v2") // the broken one is not kept to be rolled back to
	if n := strings.Count(strings.Join(r.calls, " "), "kickstart"); n != 3 {
		t.Errorf("kickstarts %d, want 3 (v2, v3, back to v2): %v", n, r.calls)
	}

	if msg, err := r.s.Rollback(); err != nil || msg != "rolled back v2 -> v1" {
		t.Fatalf("rollback: %q %v", msg, err)
	}
	r.want("v1", "v1", "v2")
	if _, err := r.s.Upgrade(r.build("v4", false)); err != nil {
		t.Fatal(err)
	}
	r.want("v4", "v1", "v4") // the last two: the one running and the one before it
	if msg, _ := r.s.Upgrade(r.build("v4", false)); msg != "already on v4" {
		t.Error(msg)
	}
	if st := r.s.Status(); !strings.Contains(st, "current: v4") || !strings.Contains(st, "running: v4") || !strings.Contains(st, ": loaded") {
		t.Error(st)
	}
}

func TestAnUpgradeWithoutAVersionChangesNothing(t *testing.T) {
	r := newRig(t)
	if _, err := r.s.Upgrade(r.build("v1", false)); err == nil || !strings.Contains(err.Error(), "not installed") {
		t.Fatalf("before install: %v", err)
	}
	r.s.Install(r.build("v1", false), false)
	bad := r.build("v2", false)
	os.WriteFile(filepath.Join(bad, "handlined"), []byte("#!/bin/sh\nexit 1\n"), 0o755)
	if _, err := r.s.Upgrade(bad); err == nil {
		t.Fatal("a binary with no version was taken")
	}
	missing := r.build("v3", false)
	os.Remove(filepath.Join(missing, "hl"))
	if _, err := r.s.Upgrade(missing); err == nil {
		t.Fatal("a folder without hl was taken")
	}
	r.want("v1", "v1")
}

func TestTheLiveLabelIsRefusedOnAScratchHome(t *testing.T) {
	r := newRig(t)
	r.s.Label = LiveLabel
	if _, err := r.s.Install(r.build("v1", false), true); err == nil || r.loaded {
		t.Fatalf("installed the live label on %s", r.s.Home)
	}
	if _, err := r.s.Uninstall(); err == nil {
		t.Fatal("uninstalled the live label from a scratch home")
	}
}

func TestARehearsalLabelKeepsItsOwnVersions(t *testing.T) {
	t.Setenv("HANDLINE_SERVICE_LABEL", "dev.handline.rehearsal")
	if s := New(t.TempDir()); filepath.Base(s.Bin) != "bin-dev.handline.rehearsal" || filepath.Base(s.Plist) != "dev.handline.rehearsal.plist" {
		t.Errorf("%s %s", s.Bin, s.Plist)
	}
}
