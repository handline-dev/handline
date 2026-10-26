package main

import (
	"bytes"
	"log"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestTheDefaultHomeIsRefusedUnlessAskedFor(t *testing.T) {
	t.Setenv("HANDLINE_HOME", "")
	t.Setenv("OCP_HOME", "")
	if h, err := resolveHome("", false); err == nil {
		t.Fatalf("started on the default home %s", h)
	}
	if h, err := resolveHome("/scratch/h", false); err != nil || h != "/scratch/h" {
		t.Fatalf("%q %v", h, err)
	}
	if _, err := resolveHome("", true); err != nil {
		t.Fatal(err)
	}
}

func TestTheRealHomeAndDisagreeingNamesAreRefused(t *testing.T) {
	u, err := os.UserHomeDir()
	if err != nil {
		t.Skip(err)
	}
	t.Setenv("HANDLINE_HOME", "")
	t.Setenv("OCP_HOME", "")
	for _, d := range []string{".handline", ".orcapipe/sub"} {
		if h, err := resolveHome(filepath.Join(u, d), false); err == nil {
			t.Fatalf("started on the real home %s", h)
		}
	}
	if _, err := resolveHome(filepath.Join(u, ".handline"), true); err != nil {
		t.Fatal(err)
	}
	a, b := t.TempDir(), t.TempDir()
	t.Setenv("HANDLINE_HOME", a)
	t.Setenv("OCP_HOME", b)
	if _, err := resolveHome(a, false); err == nil || !strings.Contains(err.Error(), "different homes") {
		t.Fatalf("disagreeing names: %v", err)
	}
	t.Setenv("OCP_HOME", a)
	if _, err := resolveHome(a, false); err != nil {
		t.Fatal(err)
	}
}

func TestEachMissingAgentToolIsOneLine(t *testing.T) {
	var out bytes.Buffer
	log.SetOutput(&out)
	t.Cleanup(func() { log.SetOutput(os.Stderr) })
	t.Setenv("PATH", t.TempDir())
	missingTools(filepath.Join(t.TempDir(), "claude"))
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	if len(lines) != 3 || !strings.Contains(lines[0], "claude not found") || !strings.Contains(lines[1], "git is not on PATH") || !strings.Contains(lines[2], "tmux is not on PATH") {
		t.Errorf("%q", out.String())
	}
}
