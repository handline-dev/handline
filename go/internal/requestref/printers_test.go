package requestref

import (
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// A message is M<n> everywhere it is printed; a #<n> is a PR. These are the lines that print a # and an id for something else, or that
// explain the difference: file (under go/) and a fragment of the line.
var hashAllowed = []struct{ file, line, why string }{
	{"internal/reads/prs.go", `"%s#%d"`, "a PR key"},
	{"internal/prompts/answer.go", `no prompt #%d`, "a prompt id"},
	{"internal/requestref/requestref.go", `#%s is PR %s`, "the refusal that says #n is a PR"},
	{"cmd/hl/taskview.go", `"#" + fmt.Sprint(tag)`, "a tag"},
	{"internal/watcher/tasks.go", `"#" + extra[id]`, "the watcher's separator"},
}

var hashPrinter = regexp.MustCompile(`#%[dvs]|"#"\s*\+|\+\s*"#"`)

func TestNoSourcePrintsAMessageIdWithAHash(t *testing.T) {
	root := filepath.Join("..", "..")
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() && (d.Name() == "vendor" || d.Name() == "testdata") {
			return filepath.SkipDir
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		b, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(root, path)
		for n, line := range strings.Split(string(b), "\n") {
			if !hashPrinter.MatchString(line) || strings.HasPrefix(strings.TrimSpace(line), "//") {
				continue
			}
			allowed := false
			for _, a := range hashAllowed {
				allowed = allowed || (a.file == filepath.ToSlash(rel) && strings.Contains(line, a.line))
			}
			if !allowed {
				t.Errorf("%s:%d prints an id with #: a message is M<n> (requestref); if this is not a message, add it to hashAllowed\n\t%s", rel, n+1, strings.TrimSpace(line))
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}
