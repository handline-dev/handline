package session

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRealpathFollowsALinkBeforeItsDotDot(t *testing.T) {
	dir, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(dir, "real", "deep"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(dir, "real", "deep"), filepath.Join(dir, "link")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("loop", filepath.Join(dir, "loop")); err != nil {
		t.Fatal(err)
	}
	for in, want := range map[string]string{
		dir + "/link/../x":         dir + "/real/x",
		dir + "/link/./f":          dir + "/real/deep/f",
		dir + "/missing/../link/f": dir + "/real/deep/f",
		dir + "//real///deep/":     dir + "/real/deep",
		dir + "/missing/a/b":       dir + "/missing/a/b",
		dir + "/loop/x":            dir + "/loop/x",
		"/..":                      "/",
	} {
		if got := realpath(in); got != want {
			t.Errorf("%s: %s, want %s", in, got, want)
		}
	}
}
