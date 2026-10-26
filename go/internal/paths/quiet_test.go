package paths

import (
	"os"
	"strings"
	"testing"
)

// The folder a daemon's claude runs in is its own: under the daemon's home, empty, and neither / nor the person's home.
func TestQuietDirIsTheDaemonsOwnEmptyFolder(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HANDLINE_HOME", home)
	dir := QuietDir()
	if !strings.HasPrefix(dir, home) || dir == home {
		t.Fatalf("%s is not under %s", dir, home)
	}
	user, _ := os.UserHomeDir()
	if dir == "/" || dir == user || dir == UserHome() {
		t.Fatalf("%s is a folder a walk would cover", dir)
	}
	if entries, err := os.ReadDir(dir); err != nil || len(entries) != 0 {
		t.Fatalf("%v %v", entries, err)
	}
	if QuietDir() != dir {
		t.Fatal("not the same folder twice")
	}
}
