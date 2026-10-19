package lifecycle

import (
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
)

// resumeID is the session a claude resume names: --resume <id>, --resume=<id> or -r <id>; "" when there is none.
func resumeID(extra []string) string {
	for i, x := range extra {
		if v, ok := strings.CutPrefix(x, "--resume="); ok {
			return v
		}
		if (x == "--resume" || x == "-r") && i+1 < len(extra) {
			return extra[i+1]
		}
	}
	return ""
}

// carrySession copies the conversation a resume names into acct's config when it is saved only under another account. A move or a
// restart can put an agent on another account, and claude finds a session only in the config it runs with: without the copy the resume
// fails with "No conversation found". The copy, not a link, since the old account's file is no longer written. Nothing found anywhere
// is left to claude to refuse.
func carrySession(home, acct string, extra []string) error {
	sid := resumeID(extra)
	to, err := team.AccountConfigDir(home, acct)
	if sid == "" || err != nil || strings.ContainsAny(sid, `/\`) || sessionIn(to, sid) != "" {
		return nil
	}
	for _, other := range paths.ListAccounts(home) {
		from, err := team.AccountConfigDir(home, other)
		if other == acct || err != nil {
			continue
		}
		if file := sessionIn(from, sid); file != "" {
			project := filepath.Join(to, "projects", filepath.Base(filepath.Dir(file)))
			if err := copyFile(file, filepath.Join(project, sid+".jsonl")); err != nil {
				return err
			}
			// its subagents' transcripts and tool results, when it has any
			if dir := strings.TrimSuffix(file, ".jsonl"); isDir(dir) {
				return copyTree(dir, filepath.Join(project, sid))
			}
			return nil
		}
	}
	return nil
}

// sessionIn is <config>/projects/<project>/<sid>.jsonl when it exists, else "".
func sessionIn(config, sid string) string {
	m, _ := filepath.Glob(filepath.Join(config, "projects", "*", sid+".jsonl"))
	if len(m) == 0 {
		return ""
	}
	return m[0]
}

func isDir(p string) bool {
	fi, err := os.Lstat(p)
	return err == nil && fi.IsDir()
}

func copyTree(from, to string) error {
	return filepath.WalkDir(from, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(from, p)
		switch {
		case d.IsDir():
			return os.MkdirAll(filepath.Join(to, rel), 0o700)
		case d.Type().IsRegular(): // a link is not followed out of the account's tree
			return copyFile(p, filepath.Join(to, rel))
		}
		return nil
	})
}

func copyFile(from, to string) error {
	if err := os.MkdirAll(filepath.Dir(to), 0o700); err != nil {
		return err
	}
	in, err := os.Open(from)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(to, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}
