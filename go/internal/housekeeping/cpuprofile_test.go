package housekeeping

import (
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

// A profile is written to a new file under the directory, which is made if it is missing, and the line says where.
func TestACPUProfileIsWrittenUnderTheDirectory(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "logs")
	said := profileCPU(dir, 50*time.Millisecond, nil)
	files, _ := filepath.Glob(filepath.Join(dir, "cpu-*.pprof"))
	if len(files) != 1 || !strings.Contains(said, files[0]) {
		t.Fatalf("files %v, said %q", files, said)
	}
	if st, err := os.Stat(files[0]); err != nil || st.Size() == 0 {
		t.Fatalf("an empty profile: %v %v", st, err)
	}
}

// A directory that cannot be made is said, and nothing panics.
func TestAProfileThatCannotBeWrittenSaysWhy(t *testing.T) {
	file := filepath.Join(t.TempDir(), "file")
	os.WriteFile(file, nil, 0o600)
	if said := profileCPU(filepath.Join(file, "logs"), time.Millisecond, nil); !strings.HasPrefix(said, "cpu profile: ") {
		t.Fatalf("%q", said)
	}
}

// A stop while a profile runs ends it early, and by the time stop returns the file holds the profile and its line was said once.
func TestAStopMidProfileWritesItBeforeItReturns(t *testing.T) {
	dir := t.TempDir()
	var mu sync.Mutex
	var said []string
	stop := ProfileCPUOnUSR2(dir, func(s string) { mu.Lock(); said = append(said, s); mu.Unlock() })
	syscall.Kill(os.Getpid(), syscall.SIGUSR2)
	var files []string
	for i := 0; i < 200 && len(files) == 0; i++ {
		time.Sleep(10 * time.Millisecond)
		files, _ = filepath.Glob(filepath.Join(dir, "cpu-*.pprof"))
	}
	if len(files) != 1 {
		t.Fatalf("no profile started: %v", files)
	}
	time.Sleep(200 * time.Millisecond)
	stop()
	mu.Lock()
	defer mu.Unlock()
	if st, err := os.Stat(files[0]); err != nil || st.Size() == 0 || len(said) != 1 || !strings.Contains(said[0], files[0]) {
		t.Fatalf("after stop: %v %v, said %q", st, err, said)
	}
}
