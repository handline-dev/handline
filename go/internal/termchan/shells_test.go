package termchan

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/inputhold"
	"github.com/handline/handline/go/internal/term"
)

// shellHost is the fake host with plain shells: one it opens is %9, and it lists what it opened.
type shellHost struct {
	*fakeHost
	shells []term.Shell
}

func (h *shellHost) CreateShell(cwd, name string) (string, string) {
	h.shells = append(h.shells, term.Shell{Handle: "%9", Name: name, Cwd: cwd, Command: "zsh"})
	return "%9", ""
}

func (h *shellHost) Shells() ([]term.Shell, bool) { return h.shells, true }

func shellRig(t *testing.T) (rig, *shellHost) {
	r := newRig(t)
	sh := &shellHost{fakeHost: r.h}
	r.e.Backend = func() term.Backend { return sh }
	r.e.Holds = &inputhold.Store{DB: r.e.DB, Changed: r.e.HoldersChanged, Now: r.e.Now}
	return r, sh
}

// A shell opens in a folder, is listed with what runs in it, and is typed into and resized by whoever holds its input, at any size.
func TestAShellOpensIsListedAndIsTypedIntoByItsHolder(t *testing.T) {
	r, _ := shellRig(t)
	pat := person("pat")
	dir := t.TempDir()
	if _, err := r.e.newShell(pat, map[string]any{"cwd": "relative"}); err == nil {
		t.Fatal("a relative folder was taken")
	}
	if _, err := r.e.newShell(pat, map[string]any{"cwd": dir + "/missing"}); err == nil {
		t.Fatal("a missing folder was taken")
	}
	out, err := r.e.newShell(pat, map[string]any{"cwd": dir, "name": " build\nlogs "})
	if err != nil {
		t.Fatal(err)
	}
	if m := out.(map[string]any); m["terminal"] != "%9" || m["name"] != "build logs" || m["cwd"] != dir {
		t.Fatalf("%v", m)
	}
	list, _ := r.e.shells(pat, nil)
	if s := list.(map[string]any)["shells"].([]any); len(s) != 1 || s[0].(map[string]any)["command"] != "zsh" {
		t.Fatalf("%v", list)
	}
	in := map[string]any{"terminal": "%9", "data": "bHM="}
	if _, err := r.e.input(pat, in); gate(t, err) != "input_holder" {
		t.Fatalf("the daemon holds a new shell's input: %v", err)
	}
	if won, err := r.e.Holds.Take(term.ShellKey("%9"), "person", "pat"); !won || err != nil {
		t.Fatal(won, err)
	}
	if _, err := r.e.input(pat, in); err != nil {
		t.Fatal(err)
	}
	if _, err := r.e.resize(pat, map[string]any{"terminal": "%9", "cols": json.Number("40"), "rows": json.Number("20")}); err != nil {
		t.Fatalf("a shell takes a phone's size, under the agent floor: %v", err)
	}
	r.agent(t)
	r.e.Holds.Take("ann", "person", "pat")
	if _, err := r.e.resize(pat, map[string]any{"terminal": "%1", "cols": json.Number("40"), "rows": json.Number("20")}); err == nil ||
		!strings.Contains(err.Error(), "never resized under 80 by 24") {
		t.Fatalf("an agent's terminal keeps its floor: %v", err)
	}
}
