package weblogin

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// takesACode prints its URL, reads one line from stdin into a file, then leaves its credential: a login that is finished by the code.
const takesACode = `echo "Open: ` + fakeURL + `"
read code
echo "$code" > "$CLAUDE_CONFIG_DIR/got"
touch "$CLAUDE_CONFIG_DIR/.credentials.json"
`

func TestACodeReachesTheChildOnceAndTheLoginEndsDone(t *testing.T) {
	r := newRig(t, takesACode)
	l, err := r.m.Start("main")
	if err != nil {
		t.Fatal(err)
	}
	if err := r.m.SendCode(l.ID, "abc-123#xyz"); err != nil {
		t.Fatal(err)
	}
	if got := r.m.Of("main"); got.URL != "" {
		t.Fatalf("a login that has its code has no URL to hand out: %+v", got)
	}
	r.settles("main", Done)
	b, _ := os.ReadFile(filepath.Join(r.home, "accounts", "main", ".claude", "got"))
	if strings.TrimSpace(string(b)) != "abc-123#xyz" {
		t.Fatalf("the child read %q", b)
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.moved) < 3 { // waiting, code_sent, done
		t.Fatalf("the accounts topic was told %v", r.moved)
	}
}

func TestASecondCodeAnUnknownLoginAndABadCodeAreRefused(t *testing.T) {
	r := newRig(t, waitsOnStdin)
	l, err := r.m.Start("main")
	if err != nil {
		t.Fatal(err)
	}
	if err := r.m.SendCode("login_nope", "x"); !errors.Is(err, ErrUnknown) {
		t.Fatalf("unknown: %v", err)
	}
	for _, bad := range []string{"", "two\nlines", "cr\rhere", strings.Repeat("a", codeMax+1)} {
		if err := r.m.SendCode(l.ID, bad); !errors.Is(err, ErrBadCode) {
			t.Fatalf("%q: %v", bad, err)
		}
	}
	if r.state("main") != Waiting {
		t.Fatal("a refused code does not move the login")
	}
	if err := r.m.SendCode(l.ID, "first"); err != nil {
		t.Fatal(err)
	}
	var no *Refusal
	err = r.m.SendCode(l.ID, "second")
	if !errors.As(err, &no) || strings.Contains(err.Error(), "second") {
		t.Fatalf("a second code is refused without the code in the error: %v", err)
	}
	if r.state("main") != CodeSent {
		t.Fatalf("state %q", r.state("main"))
	}
	if _, err := r.m.Start("main"); err == nil {
		t.Fatal("a login that has its code is still logging in")
	}
	if err := r.m.Cancel(l.ID); err != nil {
		t.Fatal(err)
	}
	r.settles("main", Cancelled)
	if err := r.m.SendCode(l.ID, "late"); !errors.As(err, &no) {
		t.Fatalf("a code for an ended login: %v", err)
	}
}

func TestACodeBeforeTheURLIsHandedOutIsRefused(t *testing.T) {
	r := newRig(t, "sleep 30\n") // never prints a URL: Start waits for it
	started := make(chan struct{})
	go func() { r.m.Start("main"); close(started) }()
	var id string
	for i := 0; i < 400 && id == ""; i++ {
		if l := r.m.Of("main"); l != nil {
			id = l.ID
		}
		time.Sleep(5 * time.Millisecond)
	}
	if id == "" {
		t.Fatal("the login never showed")
	}
	var no *Refusal
	if err := r.m.SendCode(id, "early"); !errors.As(err, &no) {
		t.Fatalf("a code before the URL: %v", err)
	}
	if r.state("main") != Waiting {
		t.Fatalf("state %q", r.state("main"))
	}
	r.timer.fire(URLWait)
	<-started
}
