// Package hrwrites serves hr.decide and what it does: the founder's decision on an HR proposal, recorded where no agent can write it, and the
// change an approve makes to the roster (handline/team/hr.py, handline/daemon/rpc_team.py). A proposal is a file, hr/<id>.yaml, kept as
// the flat map hr.py writes.
package hrwrites

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// ItemKinds are the changes one proposal can ask for; a bundle holds several. StepOrder is the order a team diff is applied in: agents exist
// before anything names them, links come before an agent is retired, and the standing context of what is left is last.
var (
	itemKinds = []string{"add", "retire", "rename", "link", "unlink", "team", "settings", "context", "schedule", "global"}
	stepOrder = []string{"team", "add", "rename", "settings", "schedule", "link", "unlink", "retire", "context", "global"}
)

const bundle = "bundle"

func contains(xs []string, x string) bool {
	for _, y := range xs {
		if y == x {
			return true
		}
	}
	return false
}

// Prop is one proposal as its file holds it, in file order.
type Prop struct{ pairs []yamlcompat.Pair }

func (p *Prop) get(k string) any {
	for _, x := range p.pairs {
		if x.Key == k {
			return x.Val
		}
	}
	return nil
}

func (p *Prop) has(k string) bool {
	for _, x := range p.pairs {
		if x.Key == k {
			return true
		}
	}
	return false
}

// str is the value as text, "" for none.
func (p *Prop) str(k string) string {
	switch v := p.get(k).(type) {
	case nil:
		return ""
	case string:
		return v
	default:
		return fmt.Sprint(v)
	}
}

func (p *Prop) set(k string, v any) {
	for i := range p.pairs {
		if p.pairs[i].Key == k {
			p.pairs[i].Val = v
			return
		}
	}
	p.pairs = append(p.pairs, yamlcompat.Pair{Key: k, Val: v})
}

func (p *Prop) pop(k string) {
	for i := range p.pairs {
		if p.pairs[i].Key == k {
			p.pairs = append(p.pairs[:i:i], p.pairs[i+1:]...)
			return
		}
	}
}

func (p *Prop) clone() *Prop { return &Prop{append([]yamlcompat.Pair(nil), p.pairs...)} }

func (p *Prop) id() string   { return p.str("id") }
func (p *Prop) kind() string { return p.str("kind") }

// sets are the `set.<key>` values of the proposal.
func (p *Prop) sets() map[string]string {
	out := map[string]string{}
	for _, x := range p.pairs {
		if strings.HasPrefix(x.Key, "set.") {
			out[x.Key[4:]] = fmt.Sprint(x.Val)
		}
	}
	return out
}

// members are the full names of a team's members.
func (p *Prop) members() []string {
	var out []string
	for _, m := range strings.Split(p.str("members"), ",") {
		if m != "" {
			out = append(out, m)
		}
	}
	return out
}

var itemKey = regexp.MustCompile(`^i(\d+)\.(.+)$`)

// items are the steps of a team diff, which the file holds as flat `i<n>.<field>` keys, as the maps the one-change drafts are.
func (p *Prop) items() []*Prop {
	grouped := map[int]*Prop{}
	var nums []int
	for _, x := range p.pairs {
		m := itemKey.FindStringSubmatch(x.Key)
		if m == nil {
			continue
		}
		var n int
		fmt.Sscan(m[1], &n)
		if grouped[n] == nil {
			grouped[n] = &Prop{}
			nums = append(nums, n)
		}
		grouped[n].set(m[2], x.Val)
	}
	sort.Ints(nums)
	out := make([]*Prop, len(nums))
	for i, n := range nums {
		out[i] = grouped[n]
	}
	return out
}

// ordered are the steps in the order they are applied (the order given, within a kind).
func ordered(items []*Prop) []*Prop {
	out := append([]*Prop(nil), items...)
	sort.SliceStable(out, func(i, j int) bool { return stepIndex(out[i].kind()) < stepIndex(out[j].kind()) })
	return out
}

func stepIndex(kind string) int {
	for i, k := range stepOrder {
		if k == kind {
			return i
		}
	}
	return len(stepOrder)
}

var subjectFields = []string{"kind", "agent", "to", "team", "repo", "model", "effort", "vendor", "response", "relation", "requested_by", "why", "members"}

// subject is what the founder is deciding on: every field of the draft that changes what applying it does, as a digest (decisions.digest).
func (p *Prop) subject() string {
	fields := map[string]any{}
	for _, k := range subjectFields {
		fields[k] = p.get(k)
	}
	for _, x := range p.pairs {
		if strings.HasPrefix(x.Key, "repo.") || strings.HasPrefix(x.Key, "set.") || itemKey.MatchString(x.Key) {
			fields[x.Key] = x.Val
		}
	}
	sum := sha256.Sum256([]byte(rpc.PyJSON(fields, true, true)))
	return hex.EncodeToString(sum[:])
}

func (e *Env) dir() string { return filepath.Join(e.Home, "hr") }

func (e *Env) path(pid string) string { return filepath.Join(e.dir(), pid+".yaml") }

// isProposal is whether a parsed file is a proposal at all.
func isProposal(p *Prop) bool {
	return p.has("id") && (contains(itemKinds, p.kind()) || p.kind() == bundle) && truthy(p.get("agent"))
}

func truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case string:
		return x != ""
	case bool:
		return x
	case int64:
		return x != 0
	}
	return true
}

func parseProp(text string) (*Prop, bool, error) {
	pairs, isMap, err := yamlcompat.ParsePairs(text)
	if err != nil || !isMap {
		return nil, false, err
	}
	return &Prop{pairs}, true, nil
}

// listing is every proposal that can be read; a file that cannot be is left out.
func (e *Env) listing() []*Prop {
	files, _ := filepath.Glob(filepath.Join(e.dir(), "*.yaml"))
	sort.Strings(files)
	var out []*Prop
	for _, f := range files {
		b, err := os.ReadFile(f)
		if err != nil {
			continue
		}
		if p, ok, err := parseProp(string(b)); err == nil && ok && isProposal(p) {
			out = append(out, p)
		}
	}
	return out
}

// load is the proposal with this id, nil when there is none or the file is not a map.
func (e *Env) load(pid string) (*Prop, error) {
	b, err := os.ReadFile(e.path(pid))
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	p, ok, err := parseProp(string(b))
	if err != nil || !ok {
		return nil, err
	}
	return p, nil
}

// save writes the proposal in one step.
func (e *Env) save(p *Prop) error {
	text, err := yamlcompat.Dump(p.pairs)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(e.dir(), 0o755); err != nil {
		return err
	}
	return writeAtomic(e.path(p.id()), text)
}

func writeAtomic(path, text string) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*.tmp")
	if err != nil {
		return err
	}
	_, werr := tmp.WriteString(text)
	if werr == nil {
		werr = tmp.Chmod(0o644)
	}
	if cerr := tmp.Close(); werr == nil {
		werr = cerr
	}
	if werr != nil {
		os.Remove(tmp.Name())
		return werr
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		os.Remove(tmp.Name())
		return err
	}
	return nil
}

// setPairs are the `set.<key>` values of the proposal in file order.
func (p *Prop) setPairs() [][2]string {
	var out [][2]string
	for _, x := range p.pairs {
		if strings.HasPrefix(x.Key, "set.") {
			out = append(out, [2]string{x.Key[4:], fmt.Sprint(x.Val)})
		}
	}
	return out
}
