package main

import (
	"bytes"
	"log"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/service"
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

func TestEnsureLineExitsOneWhenTheCarriedVersionIsNotRunning(t *testing.T) {
	for state, code := range map[string]int{"running": 0, "started": 0, "upgraded": 0, "kept": 1, "failed": 1} {
		line, got := ensureLine(service.Ensured{State: state, Version: "1.0.0+aaaaaaa", Reason: "r"})
		if got != code || line != `{"state":"`+state+`","version":"1.0.0+aaaaaaa","reason":"r"}` {
			t.Errorf("%s: %s %d", state, line, got)
		}
	}
}

func TestEnsureArgsTakeTheGatewayAddress(t *testing.T) {
	for _, c := range []struct {
		args              []string
		ok                bool
		src, listen, name string
	}{
		{nil, true, ownDir(), "", ""},
		{[]string{"/A.app"}, true, "/A.app", "", ""},
		{[]string{"--listen", "100.1.2.3:7443", "--advertise", "mac.ts.net", "/A.app"}, true, "/A.app", "100.1.2.3:7443", "mac.ts.net"},
		{[]string{"--advertise", "", "--listen", "127.0.0.1:7443"}, true, ownDir(), "127.0.0.1:7443", ""},
		{[]string{"--advertise", "mac.ts.net"}, false, "", "", ""}, // a name with no address
		{[]string{"--listen", ""}, false, "", "", ""},
		{[]string{"--listen"}, false, "", "", ""},
		{[]string{"/A.app", "/B.app"}, false, "", "", ""},
		{[]string{"--force"}, false, "", "", ""},
	} {
		s := &service.Service{}
		src, ok := ensureArgs(s, c.args)
		if ok != c.ok || (ok && (src != c.src || s.Listen != c.listen || s.Advertise != c.name)) {
			t.Errorf("%q: %q %v listen %q advertise %q", c.args, src, ok, s.Listen, s.Advertise)
		}
	}
}
