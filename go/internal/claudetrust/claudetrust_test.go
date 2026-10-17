package claudetrust

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// claudeShaped is a config as claude writes it: two-space indent, keys in its order, a unicode escape and a big number to keep.
const claudeShaped = `{
  "numStartups": 12,
  "oauthAccount": {
    "accountUuid": "x"
  },
  "projects": {
    "/other": {
      "allowedTools": [],
      "hasTrustDialogAccepted": false,
      "lastCost": 1.50
    },
    "/wt": {
      "allowedTools": ["Read"],
      "exampleFiles": "café"
    }
  },
  "userID": 12345678901234567890
}
`

func write(t *testing.T, text string) string {
	t.Helper()
	f := filepath.Join(t.TempDir(), ".claude.json")
	if err := os.WriteFile(f, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return f
}

func grant(t *testing.T, f string, keys ...string) Outcome {
	t.Helper()
	out, err := Grant(f, keys)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func readText(t *testing.T, f string) string {
	t.Helper()
	b, err := os.ReadFile(f)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestAnExistingEntryGainsTheFlagAndNoOtherByteMoves(t *testing.T) {
	f := write(t, claudeShaped)
	if out := grant(t, f, "/wt"); out != Granted {
		t.Fatalf("outcome %s", out)
	}
	want := strings.Replace(claudeShaped, `"exampleFiles": "café"`, `"exampleFiles": "café",
      "hasTrustDialogAccepted": true`, 1)
	if got := readText(t, f); got != want {
		t.Fatalf("got:\n%s\nwant:\n%s", got, want)
	}
	if out := grant(t, f, "/wt"); out != Unchanged {
		t.Fatalf("second grant: %s", out)
	}
}

func TestAFalseFlagIsTurnedTrueInPlace(t *testing.T) {
	f := write(t, claudeShaped)
	grant(t, f, "/other")
	want := strings.Replace(claudeShaped, `"hasTrustDialogAccepted": false`, `"hasTrustDialogAccepted": true`, 1)
	if got := readText(t, f); got != want {
		t.Fatalf("got:\n%s", got)
	}
}

func TestANewFolderIsAddedAsTheLastProject(t *testing.T) {
	f := write(t, claudeShaped)
	grant(t, f, "/new/wt", "/private/new/wt")
	want := strings.Replace(claudeShaped, `"café"
    }`, `"café"
    },
    "/new/wt": {"hasTrustDialogAccepted": true},
    "/private/new/wt": {"hasTrustDialogAccepted": true}`, 1)
	if got := readText(t, f); got != want {
		t.Fatalf("got:\n%s", got)
	}
}

func TestAConfigWithoutProjectsGetsThem(t *testing.T) {
	for in, want := range map[string]string{
		`{}`:                        `{"projects": {"/wt": {"hasTrustDialogAccepted": true}}}`,
		"{\n  \"a\": 1\n}":          "{\n  \"a\": 1,\n  \"projects\": {\"/wt\": {\"hasTrustDialogAccepted\": true}}\n}",
		`{"projects":{"/wt":null}}`: `{"projects":{"/wt":{"hasTrustDialogAccepted": true}}}`,
		`{"projects":{"/wt":{}}}`:   `{"projects":{"/wt":{"hasTrustDialogAccepted": true}}}`,
	} {
		f := write(t, in)
		grant(t, f, "/wt")
		if got := readText(t, f); got != want {
			t.Errorf("%s: got %s, want %s", in, got, want)
		}
		var v any
		if json.Unmarshal([]byte(readText(t, f)), &v) != nil {
			t.Errorf("%s: result is not JSON", in)
		}
	}
}

func TestOneTrustedSpellingIsEnough(t *testing.T) {
	f := write(t, `{"projects":{"/private/wt":{"hasTrustDialogAccepted":true}}}`)
	if out := grant(t, f, "/wt", "/private/wt"); out != Unchanged {
		t.Fatalf("outcome %s", out)
	}
}

func TestWhatCannotBeRewrittenIsLeftAlone(t *testing.T) {
	for _, text := range []string{`{"projects": [1]}`, `{"projects": `, `[]`, ``} {
		f := write(t, text)
		if out := grant(t, f, "/wt"); out != Unreadable {
			t.Errorf("%q: outcome %s", text, out)
		}
		if readText(t, f) != text {
			t.Errorf("%q was rewritten", text)
		}
	}
	if out := grant(t, filepath.Join(t.TempDir(), ".claude.json"), "/wt"); out != Missing {
		t.Fatalf("no file: %s", out)
	}
}

func TestClaudesLockIsNeverBroken(t *testing.T) {
	sleep = func(time.Duration) {}
	defer func() { sleep = time.Sleep }()
	f := write(t, `{}`)
	if err := os.Mkdir(f+".lock", 0o755); err != nil {
		t.Fatal(err)
	}
	if out := grant(t, f, "/wt"); out != Locked {
		t.Fatalf("outcome %s", out)
	}
	if readText(t, f) != `{}` {
		t.Fatal("written under claude's lock")
	}
	if _, err := os.Stat(f + ".lock"); err != nil {
		t.Fatal("claude's lock was removed")
	}
}

func TestTheModeAndALinkAreKept(t *testing.T) {
	dir := t.TempDir()
	real := filepath.Join(dir, "real.json")
	os.WriteFile(real, []byte(`{}`), 0o640)
	link := filepath.Join(dir, ".claude.json")
	os.Symlink(real, link)
	grant(t, link, "/wt")
	if st, _ := os.Lstat(link); st.Mode()&os.ModeSymlink == 0 {
		t.Fatal("the link was replaced by a file")
	}
	if st, _ := os.Stat(real); st.Mode().Perm() != 0o640 {
		t.Fatalf("mode %v", st.Mode().Perm())
	}
	if !strings.Contains(readText(t, real), "hasTrustDialogAccepted") {
		t.Fatal("not written through the link")
	}
	if left, _ := filepath.Glob(filepath.Join(dir, "*.tmp")); len(left) != 0 {
		t.Fatalf("temp files left: %v", left)
	}
}

// A write claude makes without its lock, landing between our read and our rename, is kept: the grant is redone on top of it.
func TestAWriteThatLandsUnderUsIsKept(t *testing.T) {
	f := write(t, `{"a": 1}`)
	n := 0
	preSwap = func() {
		if n++; n == 1 {
			os.WriteFile(f, []byte(`{"a": 1, "b": 2}`), 0o600)
		}
	}
	defer func() { preSwap = nil }()
	if out := grant(t, f, "/wt"); out != Granted {
		t.Fatalf("outcome %s", out)
	}
	if got, want := readText(t, f), `{"a": 1, "b": 2, "projects": {"/wt": {"hasTrustDialogAccepted": true}}}`; got != want {
		t.Fatalf("got %s", got)
	}
}

// Grants and a writer that holds claude's lock as claude does race on one file; at the end every key each of them added is there.
func TestConcurrentWritersLoseNoKey(t *testing.T) {
	sleep = func(time.Duration) { time.Sleep(time.Millisecond) }
	defer func() { sleep = time.Sleep }()
	f := write(t, "{\n  \"projects\": {}\n}\n")
	var wg sync.WaitGroup
	const n = 20
	for i := range n {
		wg.Add(2)
		go func() {
			defer wg.Done()
			for {
				if out, err := Grant(f, []string{fmt.Sprintf("/wt%d", i)}); err != nil || out == Granted {
					return
				}
			}
		}()
		go func() { // claude: under <file>.lock, read, add its own key, write in place
			defer wg.Done()
			for {
				release, ok := takeLock(f + ".lock")
				if !ok {
					continue
				}
				b, _ := os.ReadFile(f)
				var v map[string]any
				json.Unmarshal(b, &v)
				v[fmt.Sprintf("claude%d", i)] = i
				out, _ := json.MarshalIndent(v, "", "  ")
				os.WriteFile(f, out, 0o600)
				release()
				return
			}
		}()
	}
	wg.Wait()
	var v map[string]any
	if err := json.Unmarshal([]byte(readText(t, f)), &v); err != nil {
		t.Fatal(err)
	}
	ps := v["projects"].(map[string]any)
	for i := range n {
		if _, ok := v[fmt.Sprintf("claude%d", i)]; !ok {
			t.Errorf("claude%d lost", i)
		}
		if e, _ := ps[fmt.Sprintf("/wt%d", i)].(map[string]any); e["hasTrustDialogAccepted"] != true {
			t.Errorf("/wt%d not trusted", i)
		}
	}
}

func TestTheLegacyConfigWins(t *testing.T) {
	dir := t.TempDir()
	if got := ConfigFile(dir); got != filepath.Join(dir, ".claude.json") {
		t.Fatal(got)
	}
	os.WriteFile(filepath.Join(dir, ".config.json"), []byte(`{}`), 0o600)
	if got := ConfigFile(dir); got != filepath.Join(dir, ".config.json") {
		t.Fatal(got)
	}
}
