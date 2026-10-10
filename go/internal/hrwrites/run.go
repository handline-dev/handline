package hrwrites

import (
	"database/sql"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/sealed"
	"github.com/handline/handline/go/internal/term"
)

// Carrying out a proposal: one change, or every step of a team diff, in order. A step that is refused or raises puts back everything the steps
// before it did, and nothing of the diff stays.

// The grant kept in the protected store for an agent, which a snapshot keeps with its folder.
const skipGrantNS = "skip_permissions"

// snapshot is what a change to the roster can touch, kept so a step that fails puts all of it back: the routing table and its backup, the
// config, the folder of each agent a step names (and what lands in the archive), the seals and grants kept for them in the protected store.
// The database is put back by the transaction the steps run in.
type snapshot struct {
	home     string
	dir      string
	files    map[string][]byte // nil: the file was not there
	names    []string
	folders  map[string]string
	archived map[string]bool
	kept     map[[2]string]*string
	store    *protected.Store
}

func takeSnapshot(home string, names map[string]bool) (*snapshot, error) {
	dir, err := os.MkdirTemp(home, "hr-snapshot-")
	if err != nil {
		return nil, err
	}
	s := &snapshot{home: home, dir: dir, files: map[string][]byte{}, folders: map[string]string{}, archived: map[string]bool{}, kept: map[[2]string]*string{},
		store: protected.New(home)}
	for _, p := range []string{paths.TeamFile(home), paths.TeamFile(home) + ".bak", paths.ConfigFile(home)} {
		if b, err := os.ReadFile(p); err == nil {
			s.files[p] = b
		} else {
			s.files[p] = nil
		}
	}
	for n := range names {
		s.names = append(s.names, n)
	}
	sortStrings(s.names)
	for i, n := range s.names {
		d := paths.AgentDir(home, n)
		if st, err := os.Stat(d); err == nil && st.IsDir() {
			to := filepath.Join(dir, fmt.Sprint(i))
			if err := copyTree(d, to); err != nil {
				s.drop()
				return nil, err
			}
			s.folders[n] = to
		}
	}
	if entries, err := os.ReadDir(filepath.Join(home, "agents", "archive")); err == nil {
		for _, c := range entries {
			s.archived[c.Name()] = true
		}
	}
	keys := [][2]string{{sealed.NS, sealed.Team}}
	for _, n := range s.names {
		keys = append(keys, [2]string{sealed.NS, sealed.AgentKey(n)}, [2]string{skipGrantNS, n})
	}
	for _, k := range keys {
		if v, ok := s.store.Get(k[0], k[1]); ok {
			s.kept[k] = &v
		} else {
			s.kept[k] = nil
		}
	}
	return s, nil
}

func sortStrings(xs []string) {
	for i := 1; i < len(xs); i++ {
		for j := i; j > 0 && xs[j] < xs[j-1]; j-- {
			xs[j], xs[j-1] = xs[j-1], xs[j]
		}
	}
}

// copyTree copies a folder with its links as links.
func copyTree(from, to string) error {
	return filepath.WalkDir(from, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(from, p)
		dst := filepath.Join(to, rel)
		info, err := d.Info()
		if err != nil {
			return err
		}
		switch {
		case d.Type()&fs.ModeSymlink != 0:
			target, err := os.Readlink(p)
			if err != nil {
				return err
			}
			return os.Symlink(target, dst)
		case d.IsDir():
			return os.MkdirAll(dst, info.Mode().Perm()|0o700)
		default:
			b, err := os.ReadFile(p)
			if err != nil {
				return err
			}
			return os.WriteFile(dst, b, info.Mode().Perm())
		}
	})
}

func (s *snapshot) restore() {
	for p, data := range s.files {
		cur, err := os.ReadFile(p)
		switch {
		case data == nil:
			os.Remove(p)
		case err != nil || string(cur) != string(data):
			os.WriteFile(p, data, 0o644)
		}
	}
	archive := filepath.Join(s.home, "agents", "archive")
	if entries, err := os.ReadDir(archive); err == nil {
		for _, c := range entries {
			if !s.archived[c.Name()] {
				os.RemoveAll(filepath.Join(archive, c.Name()))
			}
		}
	}
	for _, n := range s.names {
		d := paths.AgentDir(s.home, n)
		os.RemoveAll(d)
		if from, ok := s.folders[n]; ok {
			copyTree(from, d)
		}
	}
	for k, v := range s.kept {
		if v == nil {
			s.store.Delete(k[0], k[1])
		} else {
			s.store.Put(k[0], k[1], *v)
		}
	}
}

func (s *snapshot) drop() { os.RemoveAll(s.dir) }

// namesOf are the agents whose folders and grants a step may change.
func namesOf(items []*Prop) map[string]bool {
	out := map[string]bool{}
	for _, it := range items {
		if it.kind() == "global" {
			continue // its name is a switch, and config.yaml is kept whole
		}
		out[it.str("agent")] = true
		if it.kind() == "rename" {
			out[it.str("to")] = true
		}
		if it.kind() == "team" {
			for _, m := range it.members() {
				out[m] = true
			}
		}
	}
	return out
}

// step is what a doer is given: the change, the transaction the database changes are made in, and what to do after the last step.
type step struct {
	*Prop
	tx    *sql.Tx
	after *[]func() string
}

// later runs fn (the terminal host's) after the last step, which has the database locked until then.
func (s step) later(fn func() string) string {
	*s.after = append(*s.after, fn)
	return ""
}

// hostServed refuses a change that needs a terminal host this daemon does not reach: only tmux, which keeps no repos or labels, is served.
func (e *Env) hostServed(items []*Prop) string {
	for _, it := range items {
		switch it.kind() {
		case "add", "team", "rename":
			if name := term.ConfiguredName(e.Home); name != "tmux" {
				return fmt.Sprintf("the terminal host %s is not served by this daemon yet: nothing was changed", name)
			}
		}
	}
	return ""
}

func (e *Env) doer(kind string) func(*Env, step) (bool, string, error) {
	return map[string]func(*Env, step) (bool, string, error){
		"add": (*Env).doAdd, "settings": (*Env).doSettings, "context": (*Env).doContext, "retire": (*Env).doRetire,
		"rename": (*Env).doRename, "link": (*Env).doLink, "unlink": (*Env).doUnlink, "team": (*Env).doTeam,
		"schedule": (*Env).doSchedule, "global": (*Env).doGlobal,
	}[kind]
}

// run carries out a proposal. A step that is refused or fails puts back everything the steps before it did; a diff says which step and why.
func (e *Env) run(prop *Prop) (bool, string, error) {
	isBundle := prop.kind() == bundle
	items := []*Prop{prop}
	if isBundle {
		items = ordered(prop.items())
	}
	if why := e.hostServed(items); why != "" {
		return false, why, nil
	}
	snap, err := takeSnapshot(e.Home, namesOf(items))
	if err != nil {
		return false, "", err
	}
	defer snap.drop()
	tx, err := e.DB.Begin()
	if err != nil {
		return false, "", err
	}
	var after []func() string
	var said []string
	fail := func(err error) (bool, string, error) {
		tx.Rollback()
		snap.restore()
		return false, "", err
	}
	for n, item := range items {
		cur := item.clone()
		if isBundle {
			cur.set("id", prop.id())
			cur.set("requested_by", prop.get("requested_by"))
			cur.set("why", prop.get("why"))
			cur.set("created", prop.get("created"))
		}
		doer := e.doer(item.kind())
		if doer == nil {
			return fail(fmt.Errorf("no applier for %s", item.kind()))
		}
		ok, text, err := doer(e, step{cur, tx, &after})
		if err != nil {
			return fail(err)
		}
		if !ok {
			tx.Rollback()
			snap.restore()
			if !isBundle {
				return false, text, nil
			}
			return false, fmt.Sprintf("step %d of %d (%s %s) failed: %s Nothing was changed: the steps before it were put back.",
				n+1, len(items), item.kind(), item.str("agent"), text), nil
		}
		said = append(said, text)
	}
	if err := tx.Commit(); err != nil {
		snap.restore()
		return false, "", err
	}
	var notes string
	for _, fn := range after {
		notes += fn()
	}
	if !isBundle {
		return true, said[0] + notes, nil
	}
	lines := make([]string, len(said))
	for i, t := range said {
		lines[i] = fmt.Sprintf("  %d. %s", i+1, t)
	}
	return true, fmt.Sprintf("team diff applied, %d changes:\n%s%s", len(items), strings.Join(lines, "\n"), notes), nil
}
