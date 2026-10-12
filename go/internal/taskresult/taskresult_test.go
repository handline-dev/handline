package taskresult

import (
	"os"
	"path/filepath"
	"testing"
)

func png(t *testing.T, dir, name, body string) string {
	t.Helper()
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, []byte("\x89PNG\r\n\x1a\n"+body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestShotsAreStagedAndOnlyFinalizeReplacesTheOnesKept(t *testing.T) {
	home, src := t.TempDir(), t.TempDir()
	old := Dir(home, 7)
	os.MkdirAll(old, 0o700)
	os.WriteFile(filepath.Join(old, "1.png"), []byte("earlier"), 0o600)
	shots, finalize, err := Stage(home, 7, []string{png(t, src, "a.png", "aa"), png(t, src, "b.png", "bbb")}, nil)
	if err != nil || len(shots) != 2 || shots[1].N != 2 || shots[1].Bytes != 11 {
		t.Fatalf("%+v %v", shots, err)
	}
	if b, _ := os.ReadFile(filepath.Join(old, "1.png")); string(b) != "earlier" {
		t.Fatalf("staging replaced the kept shots: %q", b)
	}
	if err := finalize(); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(filepath.Join(old, "2.png")); len(b) != 11 {
		t.Fatalf("%q", b)
	}
	if _, err := os.Stat(old + ".stage"); err == nil {
		t.Fatal("the staging folder is left")
	}
	if st, _ := os.Stat(old); st.Mode().Perm() != 0o700 {
		t.Fatalf("%v", st.Mode())
	}
}

func TestAShotMustBeAPNGUnderAnAllowedFolderAndNotTooMany(t *testing.T) {
	home, src, other := t.TempDir(), t.TempDir(), t.TempDir()
	good := png(t, src, "g.png", "x")
	if _, _, err := Stage(home, 1, []string{good}, []string{src}); err != nil {
		t.Fatal(err)
	}
	if _, _, err := Stage(home, 1, []string{png(t, other, "o.png", "x")}, []string{src}); err == nil {
		t.Fatal("a file outside the folders was taken")
	}
	many := make([]string, MaxShots+1)
	for i := range many {
		many[i] = good
	}
	if _, _, err := Stage(home, 1, many, nil); err == nil {
		t.Fatal("more than the most was taken")
	}
	if _, _, err := Stage(home, 1, []string{"rel.png"}, nil); err == nil {
		t.Fatal("a relative path was taken")
	}
	big := filepath.Join(src, "big.png")
	os.WriteFile(big, append([]byte("\x89PNG\r\n\x1a\n"), make([]byte, MaxShot)...), 0o644)
	if _, _, err := Stage(home, 1, []string{big}, nil); err == nil {
		t.Fatal("a file over the limit was taken")
	}
}
