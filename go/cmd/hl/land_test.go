package main

import (
	"errors"
	"os/exec"
	"strings"
	"testing"
)

// fakeGh answers each gh call from replies, keyed by its subcommand ("pr view", "pr checks", "pr merge"), and records the calls.
func fakeGh(t *testing.T, replies map[string]string, fail map[string]bool) *[]string {
	t.Helper()
	var calls []string
	was := gh
	t.Cleanup(func() { gh = was })
	gh = func(args ...string) ([]byte, error) {
		calls = append(calls, strings.Join(args, " "))
		key := args[0] + " " + args[1]
		if fail[key] {
			return []byte(replies[key]), &exec.ExitError{Stderr: []byte(replies[key+" stderr"])}
		}
		return []byte(replies[key]), nil
	}
	return &calls
}

const openHead = `{"headRefOid":"abc123","state":"OPEN"}`

func TestLandRefusesAPullRequestWhoseCIIsNotGreenAndNamesEachCheck(t *testing.T) {
	calls := fakeGh(t, map[string]string{"pr view": openHead,
		"pr checks": `[{"name":"go","bucket":"fail"},{"name":"apps","bucket":"pending"},{"name":"lint","bucket":"pass"},{"name":"docs","bucket":"skipping"}]`},
		map[string]bool{"pr checks": true})
	why, err := landRefusal(7)
	if err != nil || why != "#7 CI is not green: go (fail), apps (pending)" {
		t.Fatalf("%q %v", why, err)
	}
	for _, c := range *calls {
		if strings.HasPrefix(c, "pr merge") {
			t.Fatal("merged a PR whose CI is not green")
		}
	}
}

func TestLandRefusesAPullRequestCINeverRanOnOrThatIsClosed(t *testing.T) {
	fakeGh(t, map[string]string{"pr view": openHead, "pr checks stderr": "no checks reported on the 'x' branch"}, map[string]bool{"pr checks": true})
	if why, err := landRefusal(7); err != nil || why != "#7 has no CI checks: CI has not run on it" {
		t.Fatalf("%q %v", why, err)
	}
	fakeGh(t, map[string]string{"pr view": openHead, "pr checks": `[]`}, nil)
	if why, err := landRefusal(7); err != nil || why != "#7 has no CI checks: CI has not run on it" {
		t.Fatalf("%q %v", why, err)
	}
	fakeGh(t, map[string]string{"pr view": `{"headRefOid":"abc123","state":"MERGED"}`}, nil)
	if why, err := landRefusal(7); err != nil || why != "#7 is merged" {
		t.Fatalf("%q %v", why, err)
	}
}

func TestLandMergesAGreenPullRequestAtTheHeadItsChecksRanOn(t *testing.T) {
	calls := fakeGh(t, map[string]string{"pr view": openHead, "pr checks": `[{"name":"go","bucket":"pass"},{"name":"apps","bucket":"skipping"}]`}, nil)
	if why, err := landRefusal(7); err != nil || why != "" {
		t.Fatalf("%q %v", why, err)
	}
	if last := (*calls)[len(*calls)-1]; last != "pr merge 7 --merge --match-head-commit abc123" {
		t.Fatalf("%q", last)
	}
	fakeGh(t, map[string]string{"pr view": openHead, "pr checks": `[{"name":"go","bucket":"pass"}]`, "pr merge stderr": "head moved"}, map[string]bool{"pr merge": true})
	if _, err := landRefusal(7); err == nil || !strings.Contains(err.Error(), "head moved") {
		t.Fatalf("a failed merge is not said: %v", err)
	}
	gh = func(...string) ([]byte, error) { return nil, errors.New("gh: not found") }
	if _, err := landRefusal(7); err == nil {
		t.Fatal("no gh is not an error")
	}
}
