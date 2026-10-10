// Package lands is landing a branch on main, done by the daemon, which runs none of the branch's code (handline/daemon/lands.py).
//
// The daemon is outside every agent's sandbox, so it alone can write main's checkout; whatever it runs runs unsandboxed. It therefore
// runs only git, and main's own tools/localrefs.py on the message it is given. The test is the merger's, in the merger's sandbox
// (`tools/land.sh --check`), and it names the tree it tested. A sha lands when an auditor (any agent named `<team>.auditor`) recorded a
// PASS for that exact sha (`hl land --pass`) and the merger recorded it with the tree its test saw (`hl land <sha> <message> --tree
// <tree>`). The merger may also fix what is not the auditor's to judge, with `--fix`: whole files on main's tools/housekeeping.paths,
// written over the merge; anything else needs a PASS. With `--squash` main gets one commit carrying the merger's message.
//
// Both verbs are accepted only from a process of the session `hl launch` recorded for that agent, which the session's own `hl land`
// command vouched for within the minute (Grants), never on the name in the request. The daemon merges the sha into main itself, and
// fast-forwards main only if the result is the tree that was tested; if main moved meanwhile the trees differ and the merger tests
// again. A sha lands at most once.
package lands

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/gitrun"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

const (
	// Requester is who a land's result is said to the merger from.
	Requester = "land"
	// Merger is the one agent that may land a branch.
	Merger = "orcapipe.merger"

	auditorRole = "auditor"
	outputKept  = 1500
	// FixMaxBytes is the most housekeeping a land carries.
	FixMaxBytes = 1 << 20
	gitLimit    = 120 * time.Second
)

var auditorName = regexp.MustCompile(`^` + auditorRole + `\d*$`)

// Service is the lands of one daemon: the verbs' checks, the queue and the worker that merges.
type Service struct {
	DB *sql.DB
	// Repo is the repository main is checked out in; "" when the daemon was not told one.
	Repo string
	// Tell says the result of a land to the agent that asked for it.
	Tell func(to, body string)
	// Tokens proves who is on the other end of the socket and holds the session's key.
	Tokens interface {
		Verify(agent string, pid int) bool
		KeyOK(agent, key string) bool
	}
	Grants Grants
	Log    func(format string, args ...any)
	Now    func() time.Time

	mu      sync.Mutex
	running bool
	swept   bool
}

func (s *Service) log(format string, args ...any) {
	if s.Log != nil {
		s.Log(format, args...)
	}
}

func (s *Service) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}

func (s *Service) repo() string {
	if s.Repo != "" {
		return s.Repo
	}
	v, _ := paths.Getenv("LAND_REPO")
	return v
}

func (s *Service) git(cwd string, args ...string) gitrun.Result {
	if cwd == "" {
		if cwd = s.repo(); cwd == "" { // never git in the daemon's own working directory
			return gitrun.Result{RC: -1, Stderr: "no repository is configured for lands (-land-repo)"}
		}
	}
	return gitrun.RunTimeout(cwd, gitLimit, args...)
}

// commitOf is the full sha of the commit sha names in the repository, or why it is none.
func (s *Service) commitOf(sha string) (full, why string) {
	if sha == "" || strings.HasPrefix(sha, "-") { // a sha is not an option to rev-parse
		return "", fmt.Sprintf("%s is not a commit here: not a revision", sha)
	}
	r := s.git("", "rev-parse", "--verify", sha+"^{commit}")
	if r.RC == 0 {
		return strings.TrimSpace(r.Stdout), ""
	}
	out := r.Stderr
	if out == "" {
		out = r.Stdout
	}
	return "", fmt.Sprintf("%s is not a commit here: %s", sha, textsafe.Oneline(out))
}

// messageRefusal is why message may not be a merge message (personal data or a machine-local id, tools/localrefs.py), "" when it
// may. Run from main's checkout, never from the branch being landed.
func (s *Service) messageRefusal(message string) string {
	if s.repo() == "" {
		return ""
	}
	tool := filepath.Join(s.repo(), "tools", "localrefs.py")
	if st, err := os.Stat(tool); err != nil || st.IsDir() {
		return ""
	}
	f, err := os.CreateTemp("", "land-msg-*.txt")
	if err != nil {
		return "the message could not be checked (" + err.Error() + "); try again"
	}
	defer os.Remove(f.Name())
	_, werr := f.WriteString(message)
	f.Close()
	if werr != nil {
		return "the message could not be checked (" + werr.Error() + "); try again"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "python3", tool, "message", "--verbatim", f.Name())
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	err = cmd.Run()
	if ctx.Err() != nil {
		return "the message could not be checked (timed out); try again"
	}
	switch err.(type) {
	case nil:
		return ""
	case *exec.ExitError:
		return truncate(textsafe.Oneline(stderr.String()), 400)
	default:
		return "the message could not be checked (" + err.Error() + "); try again"
	}
}

func truncate(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

// IsAuditor is whether agent holds the auditor role: a name's last part is its role, numbered when a team has more than one
// (`<team>.auditor`, `<team>.auditor2`), and the roster the founder approves is the only place such a name comes from. Who is
// calling is not this name's claim: the daemon has already matched it to the session `hl launch` recorded. Any team's auditor may pass
// any team's sha.
func IsAuditor(agent string) bool {
	if agent == "" || team.ParentName(agent) == "" {
		return false
	}
	return auditorName.MatchString(agent[strings.LastIndex(agent, ".")+1:])
}

// RecordPass is the auditor's PASS for sha, from the auditor of any team.
func (s *Service) RecordPass(q db.Q, sha, caller string) (bool, string, error) {
	if !IsAuditor(caller) {
		who := caller
		if who == "" {
			who = "someone else"
		}
		return false, who + " may not record a PASS; the auditor does", nil
	}
	full, why := s.commitOf(sha)
	if full == "" {
		return false, why, nil
	}
	if _, err := q.Exec("INSERT OR REPLACE INTO land_passes(sha, by, ts) VALUES(?, ?, ?)", full, caller, s.now().Unix()); err != nil {
		return false, "", err
	}
	return true, "PASS recorded for " + short(full), nil
}

func short(sha string) string { return truncate(sha, 8) }

func (s *Service) housekeepingPaths() []string {
	if s.repo() == "" {
		return nil
	}
	b, err := os.ReadFile(filepath.Join(s.repo(), "tools", "housekeeping.paths"))
	if err != nil {
		return nil
	}
	return ReadGlobs(string(b))
}

// fixRefusal is the `--fix` payload as text by path, or why it may not land without a PASS: every path relative, inside the repo and
// on a housekeeping path, never a file agents read as instructions, every file text, and not too much of it.
func (s *Service) fixRefusal(fix any) (files map[string]string, why string) {
	m, ok := fix.(map[string]any)
	if !ok || len(m) == 0 {
		return nil, "--fix sent no files"
	}
	allowed := s.housekeepingPaths()
	total := 0
	files = map[string]string{}
	for path, v := range m {
		text, ok := v.(string)
		if !ok {
			return nil, "--fix sends paths and their text"
		}
		if why := Refusal(path, allowed); why != "" {
			return nil, why
		}
		total += len(text)
		files[path] = text
	}
	if total > FixMaxBytes {
		return nil, fmt.Sprintf("--fix sends %d bytes; housekeeping is at most %d", total, FixMaxBytes)
	}
	return files, ""
}

// RequestIn is a land as the merger sent it. Fix is the JSON of `--fix` (nil for none).
type RequestIn struct {
	Sha, Message, Tree, Caller string
	Fix                        any
	Squash                     bool
}

// Queued is a land that passed its checks, ready to be written.
type Queued struct {
	In    RequestIn
	Full  string
	Files map[string]string
}

// Check is every refusal a land has, in Python's order. A nil *Queued comes with the why.
func (s *Service) Check(q db.Q, in RequestIn) (*Queued, string, error) {
	if in.Caller != Merger {
		who := in.Caller
		if who == "" {
			who = "someone else"
		}
		return nil, who + " may not land a branch; the merger does", nil
	}
	message := strings.TrimSpace(in.Message)
	if message == "" || strings.TrimSpace(in.Tree) == "" {
		return nil, "a land needs the merge message and the tree the test saw: use `hl land --check <sha>`, then `hl land <sha> <message> --tree <tree>`", nil
	}
	if why := s.messageRefusal(message); why != "" {
		return nil, "the merge message carries personal data or a local id: " + why, nil
	}
	var files map[string]string
	if in.Fix != nil {
		var why string
		if files, why = s.fixRefusal(in.Fix); why != "" {
			return nil, why, nil
		}
	}
	full, why := s.commitOf(in.Sha)
	if full == "" {
		return nil, why, nil
	}
	var one int
	switch err := q.QueryRow("SELECT 1 FROM land_passes WHERE sha = ?", full).Scan(&one); err {
	case nil:
	case sql.ErrNoRows:
		return nil, fmt.Sprintf("no auditor PASS is recorded for %s; a PASS for another sha does not count", short(full)), nil
	default:
		return nil, "", err
	}
	var id int64
	var state string
	var merged sql.NullString
	switch err := q.QueryRow("SELECT id, state, merged FROM lands WHERE sha = ? AND state != 'failed' ORDER BY id DESC LIMIT 1", full).
		Scan(&id, &state, &merged); err {
	case nil:
		where := state
		if state == "landed" {
			where = "landed as " + short(merged.String)
		}
		return nil, fmt.Sprintf("%s is already %s (land #%d); a sha lands once", short(full), where, id), nil
	case sql.ErrNoRows:
	default:
		return nil, "", err
	}
	if s.git("", "merge-base", "--is-ancestor", full, "main").RC == 0 {
		return nil, short(full) + " is already on main", nil
	}
	in.Message, in.Tree = message, strings.TrimSpace(in.Tree)
	return &Queued{In: in, Full: full, Files: files}, "", nil
}

// Queue writes a checked land and its fix, as one write: the sweep never sees one without the other.
func (s *Service) Queue(q db.Q, c *Queued) (string, error) {
	res, err := q.Exec("INSERT INTO lands(sha, message, tree, requester, state, ts) VALUES(?, ?, ?, ?, 'queued', ?)",
		c.Full, c.In.Message, c.In.Tree, c.In.Caller, s.now().Unix())
	if err != nil {
		return "", err
	}
	id, _ := res.LastInsertId()
	if c.In.Fix != nil || c.In.Squash {
		files, _ := json.Marshal(orEmpty(c.Files))
		squash := 0
		if c.In.Squash {
			squash = 1
		}
		if _, err := q.Exec("INSERT INTO land_fixes(land_id, files, squash) VALUES(?, ?, ?)", id, string(files), squash); err != nil {
			return "", err
		}
	}
	var ahead int
	if err := q.QueryRow("SELECT COUNT(*) FROM lands WHERE state IN ('queued', 'running') AND id < ?", id).Scan(&ahead); err != nil {
		return "", err
	}
	said := fmt.Sprintf("land #%d: %s queued", id, short(c.Full))
	if ahead > 0 {
		said += fmt.Sprintf(", %d ahead of it", ahead)
	}
	return said, nil
}

func orEmpty(m map[string]string) map[string]string {
	if m == nil {
		return map[string]string{}
	}
	return m
}

// mainWorktree is the directory that has main checked out.
func (s *Service) mainWorktree() string {
	r := s.git("", "worktree", "list", "--porcelain")
	if r.RC != 0 {
		return ""
	}
	var cur, dir string
	for _, line := range strings.Split(r.Stdout, "\n") {
		switch {
		case strings.HasPrefix(line, "worktree "):
			cur = line[len("worktree "):]
		case line == "branch refs/heads/main":
			dir = cur
		}
	}
	return dir
}

// merge puts row.sha on main in a scratch worktree, git only, writes the merger's housekeeping fix over it (a conflict may stand only
// where fix has the file), and fast-forwards main when the result is the tree that was tested: as the merge, or with squash as one
// commit on main carrying the message. (landed, the sha or why not)
func (s *Service) merge(sha, message, tree string, fix map[string]string, squash bool) (bool, string) {
	mainDir := s.mainWorktree()
	if mainDir == "" {
		return false, "no worktree has main checked out"
	}
	work, err := os.MkdirTemp("", "land.")
	if err != nil {
		return false, "could not make a scratch worktree"
	}
	defer func() {
		s.git(mainDir, "worktree", "remove", "--force", work)
		os.RemoveAll(work)
	}()
	if s.git(mainDir, "worktree", "add", "--detach", work, "main").RC != 0 {
		return false, "could not make a scratch worktree"
	}
	clean := s.git(work, "merge", "--no-ff", "-q", "-m", message, sha).RC == 0
	if !clean {
		var unfixed []string
		for _, p := range strings.Fields(s.git(work, "diff", "--name-only", "--diff-filter=U").Stdout) {
			if _, ok := fix[p]; !ok {
				unfixed = append(unfixed, p)
			}
		}
		if len(unfixed) > 0 {
			return false, "conflicts with main in: " + strings.Join(unfixed, " ")
		}
	}
	if len(fix) > 0 {
		// against main's list as it is now, not as it was when the land was queued
		raw := make(map[string]any, len(fix))
		for p, t := range fix {
			raw[p] = t
		}
		if _, why := s.fixRefusal(raw); why != "" {
			return false, why
		}
		if why := WriteFiles(work, fix); why != "" {
			return false, why
		}
		// Into the merge commit itself: amended when the merge went through, the merge's own commit when it stopped.
		commit := []string{"-m", message}
		if clean {
			commit = []string{"--amend", "--no-edit"}
		}
		add := append([]string{"add", "--"}, sortedKeys(fix)...)
		if s.git(work, add...).RC != 0 || s.git(work, append([]string{"commit", "-q", "--no-verify"}, commit...)...).RC != 0 {
			return false, "the housekeeping fix could not be committed over the merge"
		}
	}
	got := strings.TrimSpace(s.git(work, "rev-parse", "HEAD^{tree}").Stdout)
	if got != tree {
		return false, "main moved since the merger's test: the merge is not the tree it tested; test again"
	}
	merged := strings.TrimSpace(s.git(work, "rev-parse", "HEAD").Stdout)
	if squash {
		r := s.git(work, "commit-tree", got, "-p", "HEAD^1", "-m", message)
		if r.RC != 0 {
			return false, "the squashed commit could not be made"
		}
		merged = strings.TrimSpace(r.Stdout)
	}
	if s.git(mainDir, "merge", "--ff-only", "-q", merged).RC != 0 {
		return false, "main moved while this ran (or its checkout is dirty); nothing landed"
	}
	return true, merged
}

func sortedKeys(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

type landRow struct {
	id                      int64
	sha, message, tree, who string
}

// runOne merges one queued land, records how it ended and tells the merger.
func (s *Service) runOne(row landRow) {
	defer func() {
		s.mu.Lock()
		s.running = false
		s.mu.Unlock()
	}()
	var fix map[string]string
	var squash bool
	var files string
	var sq int
	switch err := s.DB.QueryRow("SELECT files, squash FROM land_fixes WHERE land_id = ?", row.id).Scan(&files, &sq); err {
	case nil:
		squash = sq != 0
		var m map[string]string
		if json.Unmarshal([]byte(files), &m) == nil && len(m) > 0 {
			fix = m
		}
	case sql.ErrNoRows:
	default:
		s.log("  land #%d: %v", row.id, err)
	}
	landed, out := s.merge(row.sha, row.message, row.tree, fix, squash)
	var merged any
	if landed {
		merged = out
	}
	state := "failed"
	if landed {
		state = "landed"
	}
	if _, err := s.DB.Exec("UPDATE lands SET state = ?, merged = ?, output = ?, done_at = ? WHERE id = ?",
		state, merged, tail(out, outputKept), s.now().Unix(), row.id); err != nil {
		s.log("  land #%d: %v", row.id, err)
		return
	}
	var body string
	if landed {
		body = fmt.Sprintf("land #%d: %s is on main as %s.", row.id, short(row.sha), short(out))
	} else {
		body = fmt.Sprintf("land #%d: %s did not land. %s Test again and send it with `hl land`.", row.id, short(row.sha), textsafe.Oneline(out))
	}
	s.log("  land #%d: %s", row.id, state)
	if s.Tell != nil {
		s.Tell(row.who, body)
	}
}

func tail(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[len(r)-n:])
	}
	return s
}

// Sweep starts the oldest queued land if none is running. A row left 'running' by a daemon that stopped mid-land goes back in the
// queue: main changes only by the fast-forward at the end.
func (s *Service) Sweep() error {
	s.mu.Lock()
	if s.running {
		s.mu.Unlock()
		return nil
	}
	first := !s.swept
	s.swept = true
	s.mu.Unlock()
	if first {
		if _, err := s.DB.Exec("UPDATE lands SET state = 'queued' WHERE state = 'running'"); err != nil {
			return err
		}
	}
	var row landRow
	err := s.DB.QueryRow("SELECT id, sha, message, tree, requester FROM lands WHERE state = 'queued' ORDER BY id LIMIT 1").
		Scan(&row.id, &row.sha, &row.message, &row.tree, &row.who)
	if err == sql.ErrNoRows {
		return nil
	}
	if err != nil {
		return err
	}
	if _, err := s.DB.Exec("UPDATE lands SET state = 'running' WHERE id = ?", row.id); err != nil {
		return err
	}
	s.mu.Lock()
	s.running = true
	s.mu.Unlock()
	go s.runOne(row)
	return nil
}
