package lands

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// Which files the merger may write over a merge at land time without a PASS (`hl land --fix`), and writing them
// (handline/infra/housekeeping.py).

// A change to one of these always needs a PASS, whatever the list allows. Compared without case: the founder's disk does not tell
// CLAUDE.md from claude.md.
var (
	instructionFiles = map[string]bool{"claude.md": true, "claude.local.md": true, "agents.md": true}
	instructionDirs  = map[string]bool{".claude": true, ".git": true}
	instructionRoots = map[string]bool{"skill": true}
)

// ReadGlobs is the globs of a housekeeping.paths file: one per line, `#` starts a comment.
func ReadGlobs(text string) []string {
	var out []string
	for _, line := range strings.Split(text, "\n") {
		if i := strings.Index(line, "#"); i >= 0 {
			line = line[:i]
		}
		if g := strings.TrimSpace(line); g != "" {
			out = append(out, g)
		}
	}
	return out
}

// fnmatch is Python's fnmatch.fnmatchcase of one path component: `*`, `?`, `[seq]` and `[!seq]`.
func fnmatch(name, pat string) bool {
	var re strings.Builder
	re.WriteString("(?s)^")
	for i := 0; i < len(pat); {
		c := pat[i]
		i++
		switch c {
		case '*':
			re.WriteString(".*")
		case '?':
			re.WriteString(".")
		case '[':
			j := i
			if j < len(pat) && pat[j] == '!' {
				j++
			}
			if j < len(pat) && pat[j] == ']' {
				j++
			}
			for j < len(pat) && pat[j] != ']' {
				j++
			}
			if j >= len(pat) {
				re.WriteString(`\[`)
				continue
			}
			set := strings.ReplaceAll(pat[i:j], `\`, `\\`)
			i = j + 1
			switch {
			case strings.HasPrefix(set, "!"):
				set = "^" + set[1:]
			case strings.HasPrefix(set, "^"):
				set = `\` + set
			}
			re.WriteString("[" + set + "]")
		default:
			re.WriteString(regexp.QuoteMeta(string(c)))
		}
	}
	re.WriteString("$")
	ok, err := regexp.MatchString(re.String(), name)
	return err == nil && ok
}

// matches is glob matched one path component at a time: `*` never crosses a `/`, and a `**` component stands for any number of whole
// components, none included. Case counts.
func matches(p, glob string) bool {
	var walk func(p, g []string) bool
	walk = func(p, g []string) bool {
		if len(g) == 0 {
			return len(p) == 0
		}
		if g[0] == "**" {
			for i := 0; i <= len(p); i++ {
				if walk(p[i:], g[1:]) {
					return true
				}
			}
			return false
		}
		return len(p) > 0 && fnmatch(p[0], g[0]) && walk(p[1:], g[1:])
	}
	return walk(strings.Split(p, "/"), strings.Split(glob, "/"))
}

// Refusal is why path may not be written without a PASS, "" for none.
func Refusal(path string, globs []string) string {
	parts := strings.Split(path, "/")
	for _, p := range parts {
		if p == "" || p == "." || p == ".." {
			return fmt.Sprintf("--fix %s: not a path inside the repo", path)
		}
	}
	if path == "" || strings.HasPrefix(path, "/") || strings.Contains(path, `\`) {
		return fmt.Sprintf("--fix %s: not a path inside the repo", path)
	}
	low := make([]string, len(parts))
	for i, p := range parts {
		low[i] = strings.ToLower(p)
	}
	last := low[len(low)-1]
	inDir := false
	for _, p := range low {
		inDir = inDir || instructionDirs[p]
	}
	if instructionFiles[last] || inDir || instructionRoots[low[0]] {
		return fmt.Sprintf("--fix %s: agents read it as instructions; it needs a PASS", path)
	}
	for _, g := range globs {
		if matches(path, g) {
			return ""
		}
	}
	return fmt.Sprintf("--fix %s: not a housekeeping path (tools/housekeeping.paths); it needs a PASS", path)
}

// unsafeDest is why writing path under the worktree work would land somewhere else, "" for none: a symlink on the way (the file
// itself included) is followed by a write, out of the worktree as whoever runs it.
func unsafeDest(work, path string) string {
	here := work
	for _, part := range strings.Split(path, "/") {
		here = filepath.Join(here, part)
		if st, err := os.Lstat(here); err == nil && st.Mode()&os.ModeSymlink != 0 {
			rel, _ := filepath.Rel(work, here)
			return fmt.Sprintf("--fix %s: %s is a symlink in the merge; not written through", path, rel)
		}
	}
	root, err := filepath.EvalSymlinks(work)
	if err != nil {
		root = work
	}
	// the deepest part of the destination that exists is resolved, the rest cannot be a link yet
	dest, rest := filepath.Join(work, path), ""
	for {
		real, err := filepath.EvalSymlinks(dest)
		if err == nil {
			dest = filepath.Join(real, rest)
			break
		}
		parent := filepath.Dir(dest)
		if parent == dest {
			break
		}
		rest, dest = filepath.Join(filepath.Base(dest), rest), parent
	}
	if rel, err := filepath.Rel(root, dest); err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return fmt.Sprintf("--fix %s: resolves outside the worktree", path)
	}
	return ""
}

// WriteFiles writes each file over the worktree, checking every one before writing any. Why not, "" for done.
func WriteFiles(work string, files map[string]string) string {
	for path := range files {
		if why := unsafeDest(work, path); why != "" {
			return why
		}
	}
	for path, data := range files {
		dest := filepath.Join(work, path)
		if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
			return err.Error()
		}
		if err := os.WriteFile(dest, []byte(data), 0o644); err != nil {
			return err.Error()
		}
	}
	return ""
}
