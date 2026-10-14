package team

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// Standing consent for folders (handline/team/trust.py): which folders hl may answer the vendor's trust prompt for, on the
// founder's own say-so, never inferred from the roster. The declared roots live in the protected store, which no agent can
// write. Proposing raises the decision through the caller's Raise; applying an approved one is not here.

const (
	trustNS   = "trust"
	rootsFile = "roots.json"
	mcpFile   = "mcp_servers.json"
)

// Trust is the standing consent of one home.
type Trust struct {
	Home  string
	Store *protected.Store
}

// NewTrust is the consent kept for home.
func NewTrust(home string) *Trust { return &Trust{Home: home, Store: protected.New(home)} }

// roots is the stored list as it is, order and keys kept; none when it is missing, unreadable or not a list.
func (t *Trust) roots() []any {
	raw, ok := t.Store.Get(trustNS, rootsFile)
	if !ok {
		return nil
	}
	v, err := decodeOrdered(raw)
	if err != nil {
		return nil
	}
	list, _ := v.([]any)
	return list
}

// ListRoots is every declared root's text, in order.
func (t *Trust) ListRoots() []string {
	var out []string
	for _, r := range t.roots() {
		if o, ok := r.(*jobj); ok {
			if s, ok := o.vals["root"].(string); ok {
				out = append(out, s)
			}
		}
	}
	return out
}

// Declared is a declared root with why and who added it.
type Declared struct{ Root, Why, AddedBy string }

// Declared is every declared root with its why and who added it, in order (trust.list's declared).
func (t *Trust) Declared() []Declared {
	var out []Declared
	for _, r := range t.roots() {
		o, ok := r.(*jobj)
		if !ok {
			continue
		}
		root, ok := o.vals["root"].(string)
		if !ok {
			continue
		}
		d := Declared{Root: root, AddedBy: "?"}
		if v, has := o.vals["why"]; has {
			d.Why = pyStr(v)
		}
		if v, has := o.vals["added_by"]; has {
			d.AddedBy = pyStr(v)
		}
		out = append(out, d)
	}
	return out
}

func (t *Trust) save(rows []any) error { return t.Store.Put(trustNS, rootsFile, encodeIndent(rows)) }

func expand(p string) string {
	if p == "~" || strings.HasPrefix(p, "~/") {
		return filepath.Join(paths.UserHome(), strings.TrimPrefix(p, "~"))
	}
	return p
}

// resolved is the canonical form a root is stored and matched under: symlinks resolved as far as the path exists. ok is false
// when it cannot be resolved. One function so IsDeclared and Remove can never disagree about what a root's own spelling is.
func resolved(path string) (string, bool) {
	abs, err := filepath.Abs(expand(path))
	if err != nil {
		return "", false
	}
	rest := ""
	for cur := abs; ; cur = filepath.Dir(cur) {
		if real, err := filepath.EvalSymlinks(cur); err == nil {
			return filepath.Join(real, rest), true
		}
		if filepath.Dir(cur) == cur {
			return filepath.Clean(abs), true
		}
		rest = filepath.Join(filepath.Base(cur), rest)
	}
}

func inside(root, target string) bool {
	return target == root || strings.HasPrefix(target, strings.TrimSuffix(root, "/")+"/")
}

// IsDeclared is whether path is inside a root the founder has declared, or one of its worktrees.
func (t *Trust) IsDeclared(path string) bool {
	target, ok := resolved(path)
	if !ok {
		return false
	}
	for _, root := range t.ListRoots() {
		if r, ok := resolved(root); ok && inside(r, target) {
			return true
		}
	}
	return false
}

func entryHash(entry any) string {
	h := sha256.Sum256([]byte(encodeSorted(entry)))
	return hex.EncodeToString(h[:])[:16]
}

// MCPServers are the servers a folder's .mcp.json names, as {name: hash of its whole entry (the command, args and environment
// included)}; empty when there is no readable file.
func MCPServers(folder string) map[string]string {
	out := map[string]string{}
	b, err := os.ReadFile(filepath.Join(expand(folder), ".mcp.json"))
	if err != nil {
		return out
	}
	v, err := decodeOrdered(string(b))
	if err != nil {
		return out
	}
	o, ok := v.(*jobj)
	if !ok {
		return out
	}
	servers, ok := o.vals["mcpServers"].(*jobj)
	if !ok {
		return out
	}
	for _, k := range servers.keys {
		out[k] = entryHash(servers.vals[k])
	}
	return out
}

func (t *Trust) approvedMCP() map[string]bool {
	out := map[string]bool{}
	raw, ok := t.Store.Get(trustNS, mcpFile)
	if !ok || raw == "" {
		return out
	}
	v, err := decodeOrdered(raw)
	if err != nil {
		return out
	}
	list, _ := v.([]any)
	for _, x := range list {
		if s, ok := x.(string); ok {
			out[s] = true
		}
	}
	return out
}

// MCPApproved are the servers in a folder's .mcp.json a launch may enable without the vendor asking: the folder is under a
// declared root and the founder approved that server's entry as it is now. An agent can write the file in its own worktree, so a
// name alone is never enough.
func (t *Trust) MCPApproved(folder string) []string {
	out := []string{}
	if !t.IsDeclared(folder) {
		return out
	}
	done := t.approvedMCP()
	for n, h := range MCPServers(folder) {
		if done[n+":"+h] {
			out = append(out, n)
		}
	}
	sort.Strings(out)
	return out
}

// MCPUnapproved are the servers MCPApproved leaves to the vendor's own question.
func (t *Trust) MCPUnapproved(folder string) []string {
	approved := map[string]bool{}
	for _, n := range t.MCPApproved(folder) {
		approved[n] = true
	}
	out := []string{}
	for n := range MCPServers(folder) {
		if !approved[n] {
			out = append(out, n)
		}
	}
	sort.Strings(out)
	return out
}

// ApproveMCP is the founder approving every server in a folder's .mcp.json as it reads now, by content.
func (t *Trust) ApproveMCP(folder string) (bool, string) {
	if !t.IsDeclared(folder) {
		return false, folder + " is not inside a declared root; `hl trust add` it first"
	}
	servers := MCPServers(folder)
	if len(servers) == 0 {
		return false, folder + " has no readable .mcp.json with servers"
	}
	keep := t.approvedMCP()
	names := make([]string, 0, len(servers))
	for n, h := range servers {
		keep[n+":"+h] = true
		names = append(names, n)
	}
	list := make([]string, 0, len(keep))
	for k := range keep {
		list = append(list, k)
	}
	sort.Strings(list)
	arr := make([]any, len(list))
	for i, x := range list {
		arr[i] = x
	}
	if err := t.Store.Put(trustNS, mcpFile, encodeCompactSpaced(arr)); err != nil {
		return false, err.Error()
	}
	sort.Strings(names)
	return true, "approved: " + strings.Join(names, ", ")
}

// encodeCompactSpaced is json.dumps(list) with Python's default separators: ", " between items.
func encodeCompactSpaced(list []any) string {
	parts := make([]string, len(list))
	for i, x := range list {
		var b strings.Builder
		writeValue(&b, x)
		parts[i] = b.String()
	}
	return "[" + strings.Join(parts, ", ") + "]"
}

// Proposal is a trust proposal file, its keys in the order the file has them.
type Proposal []yamlcompat.Pair

// Get is a field of the proposal.
func (p Proposal) Get(k string) any { v, _ := pairsGet(p, k); return v }

func (t *Trust) proposalDir() (string, error) {
	d := filepath.Join(t.Home, "trust_proposals")
	return d, os.MkdirAll(d, 0o755)
}

func readProposal(path string) (Proposal, bool, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, false, err
	}
	pairs, isMap, err := yamlcompat.ParsePairs(string(b))
	return pairs, isMap, err
}

// LoadProposal is the proposal with this id; ok is false when there is none or it is not a map.
func (t *Trust) LoadProposal(id string) (Proposal, bool, error) {
	dir, err := t.proposalDir()
	if err != nil {
		return nil, false, err
	}
	p, isMap, err := readProposal(filepath.Join(dir, id+".yaml"))
	if os.IsNotExist(err) {
		return nil, false, nil
	}
	return p, isMap && err == nil, err
}

// Listing is every proposal, by file name.
func (t *Trust) Listing() ([]Proposal, error) {
	dir, err := t.proposalDir()
	if err != nil {
		return nil, err
	}
	files, err := filepath.Glob(filepath.Join(dir, "*.yaml"))
	if err != nil {
		return nil, err
	}
	sort.Strings(files)
	var out []Proposal
	for _, f := range files {
		p, isMap, err := readProposal(f)
		if err != nil {
			return nil, err
		}
		if isMap {
			out = append(out, p)
		}
	}
	return out, nil
}

// SaveProposal writes the proposal atomically.
func (t *Trust) SaveProposal(p Proposal) error {
	dir, err := t.proposalDir()
	if err != nil {
		return err
	}
	text, err := yamlcompat.Dump(p)
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(dir, ".tmp-")
	if err != nil {
		return err
	}
	if _, err := f.WriteString(text); err != nil {
		f.Close()
		os.Remove(f.Name())
		return err
	}
	if err := f.Close(); err != nil {
		os.Remove(f.Name())
		return err
	}
	return os.Rename(f.Name(), filepath.Join(dir, pyStr(p.Get("id"))+".yaml"))
}

var rootReadRe = regexp.MustCompile(`^\[trust\] add (.+) - asked by (\S+)$`)

// RootRead is the root the founder was shown: the path in the decision's own first line, and only if that line names this requester.
func RootRead(body, requester string) (string, bool) {
	first, _, _ := strings.Cut(body, "\n")
	m := rootReadRe.FindStringSubmatch(first)
	if m == nil || m[2] != requester {
		return "", false
	}
	return m[1], true
}

// DecisionText is what the founder reads when a proposal asks to add a root.
func DecisionText(root, why, requestedBy, id string) string {
	return fmt.Sprintf("[trust] add %s - asked by %s\n\n%s\n\nocp would press \"Yes, I trust this folder\" for this path and everything under it, without asking again.\n\nAnswer `approve` or `reject` (proposal %s).",
		root, requestedBy, why, id)
}

// SubjectOf is what the founder is deciding on: the root as it will be matched.
func SubjectOf(p Proposal) string {
	root := pyStr(p.Get("root"))
	if r, ok := resolved(root); ok && p.Get("root") != nil {
		return r
	}
	return root
}

// Raise opens the founder's decision on a proposal and answers its card (the request) and its decision task; ok is false, with the
// reason, when it could not be raised.
type Raise func(title, text string) (ok bool, said string, request, task int64, err error)

// Propose asks the founder to add root to standing consent: the decision is raised and the proposal saved; nothing changes yet. id is
// the proposal's when one was raised.
func (t *Trust) Propose(root, why, requester string, raise Raise) (ok bool, said, id string, err error) {
	why = strings.TrimSpace(why)
	if why == "" {
		return false, "say why: the founder is deciding, not rubber-stamping", "", nil
	}
	res, resolvedOK := resolved(root)
	if !resolvedOK {
		res = expand(root)
	}
	if t.IsDeclared(res) {
		return false, res + " is already inside a declared root", "", nil
	}
	b := make([]byte, 4)
	if _, err := rand.Read(b); err != nil {
		return false, "", "", err
	}
	id = hex.EncodeToString(b)
	text := DecisionText(res, why, requester, id)
	title := []rune(textsafe.Oneline(strings.SplitN(text, "\n", 2)[0]))
	if len(title) > 80 {
		title = title[:80]
	}
	raised, reason, rid, tid, err := raise(string(title), text)
	if err != nil || !raised {
		return false, "could not raise it: " + reason, "", err
	}
	p := Proposal{{Key: "id", Val: id}, {Key: "root", Val: res}, {Key: "why", Val: why}, {Key: "requested_by", Val: requester},
		{Key: "created", Val: time.Now().Unix()}, {Key: "state", Val: "proposed"}, {Key: "request", Val: rid}, {Key: "task", Val: tid}}
	if err := t.SaveProposal(p); err != nil {
		return false, "", "", err
	}
	return true, fmt.Sprintf("proposal %s raised as #%d. Nothing changes until the founder answers it; an approve is applied then, and you are told how it went.", id, rid), id, nil
}

// Enact is the founder adding a root directly, with no proposal to approve.
func (t *Trust) Enact(root, why string) (bool, string) {
	res, ok := resolved(root)
	if !ok {
		res = expand(root)
	}
	if t.IsDeclared(res) {
		return false, res + " is already inside a declared root"
	}
	why = strings.TrimSpace(why)
	if why == "" {
		why = "added by the founder"
	}
	row := newJobj()
	row.set("root", res)
	row.set("why", why)
	row.set("added_by", protected.Human)
	row.set("added_at", jsonInt(time.Now().Unix()))
	if err := t.save(append(t.roots(), row)); err != nil {
		return false, err.Error()
	}
	return true, res + " is now a declared root"
}

// Remove takes a root off standing consent; this only shrinks what ocp may answer for.
func (t *Trust) Remove(root string) (bool, string) {
	res, ok := resolved(root)
	if !ok {
		res = expand(root)
	}
	rows := t.roots()
	var kept []any
	for _, r := range rows {
		o, isObj := r.(*jobj)
		var rr string
		var has bool
		if isObj {
			rr, has = resolvedOf(o)
		}
		if !(has && rr == res) {
			kept = append(kept, r)
		}
	}
	if len(kept) == len(rows) {
		return false, res + " is not a declared root"
	}
	if kept == nil {
		kept = []any{}
	}
	if err := t.save(kept); err != nil {
		return false, err.Error()
	}
	return true, res + " is no longer a declared root"
}

func resolvedOf(o *jobj) (string, bool) {
	s, ok := o.vals["root"].(string)
	if !ok {
		return "", false
	}
	return resolved(s)
}

// SettleMoot settles each open proposal for a root the founder has already declared as withdrawn (never as approved), and
// returns their ids.
func (t *Trust) SettleMoot() ([]string, error) {
	props, err := t.Listing()
	if err != nil {
		return nil, err
	}
	var gone []string
	for _, p := range props {
		root, has := p.Get("root").(string)
		if p.Get("state") == "proposed" && has && root != "" && t.IsDeclared(root) {
			p = pairsSet(p, "state", "withdrawn")
			p = pairsSet(p, "verdict", "moot: that root is already declared")
			if err := t.SaveProposal(p); err != nil {
				return gone, err
			}
			gone = append(gone, pyStr(p.Get("id")))
		}
	}
	return gone, nil
}

// OneLine is textsafe.Oneline, for the verdicts a proposal keeps.
func OneLine(s string) string { return textsafe.Oneline(s) }
