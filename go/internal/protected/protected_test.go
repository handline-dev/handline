package protected

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func newStore(t *testing.T) *Store {
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	t.Setenv("OCP_AGENT", "")
	t.Setenv("HANDLINE_AGENT", "")
	return New(filepath.Join(t.TempDir(), "home"))
}

func TestPutGetDeleteAndPrivateFolders(t *testing.T) {
	s := newStore(t)
	if _, ok := s.Get("trust", "roots.json"); ok {
		t.Fatal("an entry from nowhere")
	}
	if err := s.Put("trust", "roots.json", "[]"); err != nil {
		t.Fatal(err)
	}
	if v, ok := s.Get("trust", "roots.json"); !ok || v != "[]" {
		t.Fatalf("%q %v", v, ok)
	}
	for _, d := range []string{s.Root, filepath.Join(s.Root, "trust")} {
		if st, err := os.Stat(d); err != nil || st.Mode().Perm() != 0o700 {
			t.Errorf("%s: %v %v", d, st, err)
		}
	}
	if files := s.Files(); len(files) != 1 || files[0] != filepath.Join("trust", "roots.json") {
		t.Fatalf("%v", files)
	}
	if err := s.Delete("trust", "roots.json"); err != nil {
		t.Fatal(err)
	}
	if err := s.Delete("trust", "roots.json"); err != nil {
		t.Fatalf("deleting nothing: %v", err)
	}
	if _, ok := s.Get("trust", "roots.json"); ok {
		t.Fatal("still there")
	}
}

func TestOnlyPlainNamesAreEntries(t *testing.T) {
	s := newStore(t)
	for _, bad := range [][2]string{{"..", "x"}, {"a", "../x"}, {"a/b", "x"}, {"a", ".hidden"}, {"", "x"}} {
		if err := s.Put(bad[0], bad[1], "v"); err == nil {
			t.Errorf("wrote %v", bad)
		}
		if _, ok := s.Get(bad[0], bad[1]); ok {
			t.Errorf("read %v", bad)
		}
	}
}

func TestAnAgentSessionCannotWrite(t *testing.T) {
	s := newStore(t)
	t.Setenv("OCP_AGENT", "worker")
	t.Setenv("HANDLINE_AGENT", "worker")
	for _, err := range []error{s.Put("n", "x", "v"), s.Delete("n", "x"), s.Ensure("n")} {
		if !errors.Is(err, ErrNotTheFounder) {
			t.Errorf("%v", err)
		}
	}
	t.Setenv("OCP_AGENT", "human")
	t.Setenv("HANDLINE_AGENT", "human")
	if err := s.Put("n", "x", "v"); err != nil {
		t.Fatalf("the founder's own name writes: %v", err)
	}
}

func TestIsOffReadsOnlyOff(t *testing.T) {
	s := newStore(t)
	if s.IsOff("gate", "x") {
		t.Fatal("no entry is not off")
	}
	s.Put("gate", "x", "on")
	if s.IsOff("gate", "x") {
		t.Fatal("on is not off")
	}
	s.Put("gate", "x", "off")
	if !s.IsOff("gate", "x") {
		t.Fatal("off is off")
	}
}

func TestExposureNamesAStoreInsideTheHome(t *testing.T) {
	s := newStore(t)
	s.Put("n", "x", "v")
	if got := s.Exposure(filepath.Join(t.TempDir(), "elsewhere")); len(got) != 0 {
		t.Fatalf("%v", got)
	}
	if got := s.Exposure(filepath.Dir(s.Root)); len(got) == 0 {
		t.Fatal("a store inside the home is no lock")
	}
}
