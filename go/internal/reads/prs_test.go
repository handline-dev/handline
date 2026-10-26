package reads

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os/exec"
	"reflect"
	"testing"
	"time"
)

func TestGithubRepoReadsHttpsAndSshRemotesAndNothingElse(t *testing.T) {
	for url, want := range map[string]string{
		"https://github.com/handline-dev/handline.git": "handline-dev/handline",
		"https://github.com/handline-dev/handline":     "handline-dev/handline",
		"https://user@github.com/o/r.name.git\n":       "o/r.name", // local-ref-ok: userinfo in a remote is the case
		"git@github.com:o/r.git":                       "o/r",
		"ssh://git@github.com/o/r":                     "o/r",
		"https://gitlab.com/o/r.git":                   "",
		"git@github.com.evil.example:o/r.git":          "",
		"https://github.com/o":                         "",
		"/src/repo":                                    "",
		"":                                             "",
	} {
		if got := githubRepo(url); got != want {
			t.Errorf("%q: got %q, want %q", url, got, want)
		}
	}
}

// prAgent is an agent whose worktree is a git repo with the given origin ("" for none).
func prAgent(t *testing.T, e *Env, name, origin string) {
	t.Helper()
	addAgent(t, e, name, "")
	dir := t.TempDir()
	run := func(args ...string) {
		if out, err := exec.Command("git", append([]string{"-C", dir}, args...)...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v %s", args, err, out)
		}
	}
	run("init", "-q")
	if origin != "" {
		run("remote", "add", "origin", origin)
	}
	if _, err := e.DB.Exec("INSERT INTO agent_state(agent, state, updated, worktree) VALUES(?, 'idle', 1, ?)", name, dir); err != nil {
		t.Fatal(err)
	}
}

func askPRs(t *testing.T, e *Env, agent string, numbers ...any) (map[string]any, error) {
	t.Helper()
	b, _ := json.Marshal(map[string]any{"agent": agent, "numbers": numbers})
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.UseNumber() // as the server decodes params
	var params map[string]any
	if err := dec.Decode(&params); err != nil {
		t.Fatal(err)
	}
	got, err := e.prExists(nil, params)
	if err != nil {
		return nil, err
	}
	return got.(map[string]any), nil
}

func TestPrExistsAsksGhOncePerBatchAndRemembersAHitForGoodAndAMissForAMinute(t *testing.T) {
	e := testEnv(t)
	e.prs = newPRCache()
	now := time.Now()
	e.prs.now = func() time.Time { return now }
	prAgent(t, e, "w", "git@github.com:o/r.git")
	var asked [][]int
	e.PullRequests = func(_ context.Context, repo string, numbers []int) ([]int, error) {
		if repo != "o/r" {
			t.Fatalf("repo %q", repo)
		}
		asked = append(asked, numbers)
		var found []int
		for _, n := range numbers {
			if n%2 == 0 {
				found = append(found, n)
			}
		}
		return found, nil
	}
	got, err := askPRs(t, e, "w", 3.0, 2.0, 2.0, 4.0)
	if err != nil || got["repo"] != "o/r" || !reflect.DeepEqual(got["found"], []int{2, 4}) {
		t.Fatalf("%v %v", got, err)
	}
	if len(asked) != 1 || !reflect.DeepEqual(asked[0], []int{3, 2, 4}) {
		t.Fatalf("one call for the batch, duplicates dropped: %v", asked)
	}
	askPRs(t, e, "w", 3.0, 2.0)
	if len(asked) != 1 {
		t.Fatalf("a hit and a fresh miss are cached: %v", asked)
	}
	now = now.Add(2 * time.Minute)
	got, _ = askPRs(t, e, "w", 3.0, 2.0)
	if len(asked) != 2 || !reflect.DeepEqual(asked[1], []int{3}) || !reflect.DeepEqual(got["found"], []int{2}) {
		t.Fatalf("only the expired miss is asked again: %v %v", asked, got)
	}
}

func TestPrExistsAnswersNothingAndCachesNothingWhenGhFails(t *testing.T) {
	e := testEnv(t)
	e.prs = newPRCache()
	prAgent(t, e, "w", "https://github.com/o/r.git")
	fail := true
	calls := 0
	e.PullRequests = func(context.Context, string, []int) ([]int, error) {
		calls++
		if fail {
			return nil, errors.New("gh: not logged in")
		}
		return []int{7}, nil
	}
	got, err := askPRs(t, e, "w", 7.0)
	if err != nil || got["repo"] != "o/r" || len(got["found"].([]int)) != 0 {
		t.Fatalf("%v %v", got, err)
	}
	fail = false
	got, _ = askPRs(t, e, "w", 7.0)
	if calls != 2 || !reflect.DeepEqual(got["found"], []int{7}) {
		t.Fatalf("the failure was cached: %d %v", calls, got)
	}
}

func TestPrExistsHasNoRepoForAnOriginThatIsNotOnGithubOrNoWorktree(t *testing.T) {
	e := testEnv(t)
	e.prs = newPRCache()
	e.PullRequests = func(context.Context, string, []int) ([]int, error) {
		t.Fatal("gh asked about a repo that is not on GitHub")
		return nil, nil
	}
	prAgent(t, e, "gitlab", "https://gitlab.com/o/r.git")
	prAgent(t, e, "bare", "")
	addAgent(t, e, "nowt", "")
	for _, name := range []string{"gitlab", "bare", "nowt"} {
		got, err := askPRs(t, e, name, 1.0)
		if err != nil || got["repo"] != nil || len(got["found"].([]int)) != 0 {
			t.Fatalf("%s: %v %v", name, got, err)
		}
	}
}

func TestPrExistsRefusesAnUnknownAgentAndBadNumbers(t *testing.T) {
	e := testEnv(t)
	e.prs = newPRCache()
	prAgent(t, e, "w", "https://github.com/o/r")
	if _, err := askPRs(t, e, "ghost", 1.0); err == nil {
		t.Fatal("an unknown agent answered")
	}
	tooMany := make([]any, 21)
	for i := range tooMany {
		tooMany[i] = float64(i + 1)
	}
	for _, nums := range [][]any{tooMany, {"3"}, {1.5}, {0.0}, {-2.0}} {
		if _, err := askPRs(t, e, "w", nums...); err == nil {
			t.Errorf("%v answered", nums)
		}
	}
	if _, err := e.prExists(nil, map[string]any{"agent": "w"}); err == nil {
		t.Error("no numbers answered")
	}
}

func TestAgentGetNamesTheRepoOfItsWorktree(t *testing.T) {
	e := testEnv(t)
	prAgent(t, e, "w", "git@github.com:o/r.git")
	got, err := e.agentGet(nil, map[string]any{"agent": "w"})
	if err != nil {
		t.Fatal(err)
	}
	if wt := got.(map[string]any)["worktree"].(map[string]any); wt["repo"] != "o/r" {
		t.Fatalf("%v", wt)
	}
}

func TestAWorktreeRecordedAsRepoIdAndPathStillNamesItsRepo(t *testing.T) {
	e := testEnv(t)
	e.prs = newPRCache()
	prAgent(t, e, "w", "https://github.com/o/r.git")
	var path string
	e.DB.QueryRow("SELECT worktree FROM agent_state WHERE agent = 'w'").Scan(&path)
	e.DB.Exec("UPDATE agent_state SET worktree = ? WHERE agent = 'w'", "repo-1::"+path)
	e.PullRequests = func(context.Context, string, []int) ([]int, error) { return []int{5}, nil }
	if got, err := askPRs(t, e, "w", 5.0); err != nil || got["repo"] != "o/r" || !reflect.DeepEqual(got["found"], []int{5}) {
		t.Fatalf("%v %v", got, err)
	}
	got, err := e.agentGet(nil, map[string]any{"agent": "w"})
	if err != nil {
		t.Fatal(err)
	}
	if wt := got.(map[string]any)["worktree"].(map[string]any); wt["repo"] != "o/r" {
		t.Fatalf("%v", wt)
	}
}

func TestAgentGetCarriesTheSettingsOfItsAgentFile(t *testing.T) {
	e := testEnv(t)
	addAgent(t, e, "w", "")
	writeAgentYAML(t, e, "w", "role: r\nmodel: opus\neffort: low\nweight: light\nrepo: handline\n")
	got, err := e.agentGet(nil, map[string]any{"agent": "w"})
	if err != nil {
		t.Fatal(err)
	}
	s, _ := got.(map[string]any)["settings"].(map[string]string)
	if s["model"] != "opus" || s["effort"] != "low" || s["weight"] != "light" || s["repo"] != "handline" {
		t.Fatalf("%v", s)
	}
	if _, has := s["session_account"]; has {
		t.Fatalf("a key outside the contract: %v", s)
	}
}
