package housekeeping

import (
	"os"
	"path/filepath"
	"strings"
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
