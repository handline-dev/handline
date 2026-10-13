package main

import (
	"bytes"
	"errors"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/devinstance"
)

func TestDevIsTheFoundersOrTheTeamManagersAndStatusIsAnyones(t *testing.T) {
	var calls []string
	e := &devinstance.Env{UserHome: t.TempDir(), LiveHome: t.TempDir(), UID: 501,
		Launchctl: func(args ...string) (string, error) {
			calls = append(calls, strings.Join(args, " "))
			return "", errors.New("not loaded")
		}}
	run := func(agent string, args ...string) (int, string) {
		var out bytes.Buffer
		code := runDev(e, agent, args, &out, &out)
		return code, out.String()
	}
	if code, out := run("", "on"); code != 2 || !strings.Contains(out, "usage: hl dev on <team>") {
		t.Fatalf("%d %s", code, out)
	}
	if code, out := run("", "off", "handline.pc", "--fresh"); code != 2 {
		t.Fatalf("--fresh is on's only: %d %s", code, out)
	}
	if code, out := run("handline.pc", "off", "handline.daemon"); code != 1 || !strings.Contains(out, "the founder's or handline.daemon's to change, not handline.pc's") {
		t.Fatalf("%d %s", code, out)
	}
	if len(calls) != 0 {
		t.Fatalf("a refusal reaches no launchd: %v", calls)
	}
	if code, out := run("handline.daemon", "off", "handline.daemon"); code != 0 || out != "handline.daemon's dev instance is off\n" {
		t.Fatalf("the manager turns its own off: %d %s", code, out)
	}
	if code, out := run("handline.pc.web", "status"); code != 0 || strings.Count(out, " off\n") != 3 {
		t.Fatalf("%d %s", code, out)
	}
}
