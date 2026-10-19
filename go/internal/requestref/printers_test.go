package requestref

import (
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// A message is printed as msg_<hash> (db.MsgName) everywhere; a #<n> is a PR. These are the lines that print a # and an id for something
// else, or that explain the difference: file (under go/) and a fragment of the line.
var hashAllowed = []struct{ file, line, why string }{
	{"internal/reads/prs.go", `"%s#%d"`, "a PR key"},
	{"internal/requestref/requestref.go", `#%s is PR %s`, "the refusal that says #n is a PR"},
	{"cmd/hl/taskview.go", `"#" + fmt.Sprint(tag)`, "a tag"},
	{"internal/watcher/tasks.go", `"#" + extra[id]`, "the watcher's separator"},
	{"internal/msgtest/msgtest.go", `fmt.Sprintf("#%d", ids[0])`, "tests read a printed name back as its id"},
	{"cmd/hl/taskview.go", `PR #%d`, "a task's PR"},
	{"internal/taskwrites/endreviews.go", `PR #%d`, "a task's PR"},
	{"internal/taskwrites/edit.go", `PR #%d`, "a task's PR"},
	{"internal/taskwrites/edit.go", `fmt.Sprintf("#%d", t.PR.Int64)`, "a task's PR"},
	{"internal/taskwrites/edit.go", `fmt.Sprintf("#%d", pr)`, "a task's PR"},
	{"internal/taskwrites/edit.go", `-> '#%d'`, "a task's PR"},
}

// A # before a verb (with flags, a width or an argument index: #%-5d, #%[1]d), a "#" joined by + , a "#" argument of a Sprint or Print
// call, which prints it before the id that follows, or a hint that names a message by its number (hl read %d, --parent %d).
var hashPrinter = regexp.MustCompile(`#%[-+ 0#]*(\d+|\[\d+\])?[dvs]|"#"\s*\+|\+\s*"#"|\b\w*[Pp]rint\w*\((.*,\s*)?"#"\s*,|hl read %d|--parent %d`)

func TestNoSourcePrintsAMessageByItsNumber(t *testing.T) {
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
				t.Errorf("%s:%d prints a message by its number: name it with db.MsgName; if this is not a message, add it to hashAllowed\n\t%s",
					rel, n+1, strings.TrimSpace(line))
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}
