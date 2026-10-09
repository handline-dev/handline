package reads

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

const (
	settledWindow = 7 * 24 * time.Hour
	settledMax    = 50
	maxError      = 400
)

var proposalKinds = map[string]bool{"add": true, "retire": true, "rename": true, "link": true, "unlink": true,
	"team": true, "settings": true, "context": true, "schedule": true, "global": true, "bundle": true}

// hrState is the wire word for a proposal file's state, as handline/team/hr.py card() draws them: "proposed" waits for the
// founder, "approved" is decided but did not go through, "applied" is done. "enacting" and "failed" are a human's own change, and are not served.
var hrState = map[string]string{"proposed": "open", "approved": "apply_failed", "applied": "approved",
	"rejected": "rejected", "withdrawn": "withdrawn"}

func sha256Hex(s string) string {
	h := sha256.Sum256([]byte(s))
	return hex.EncodeToString(h[:])
}

func rpcPyJSON(v map[string]any) string { return rpc.PyJSON(v, true, true) }

type proposal struct {
	flat
	wireID  int64
	state   string
	created int64
	settled int64
}

func (p proposal) waits() bool { return p.state == "open" || p.state == "apply_failed" }

// proposals is every proposal file under hr/ that reads as one: one that does not parse, or is not a proposal, is left out.
func (e *Env) proposals() []proposal {
	files, _ := filepath.Glob(filepath.Join(e.Home, "hr", "*.yaml"))
	sort.Strings(files)
	var out []proposal
	for _, file := range files {
		raw, err := os.ReadFile(file)
		if err != nil {
			continue
		}
		f, err := parseFlat(string(raw))
		if err != nil || !f.truthy("id") || !proposalKinds[f.text("kind")] || !f.truthy("agent") {
			continue
		}
		id, ok := f.get("id").(string)
		if !ok || id == "" || strings.Trim(id, "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ") != "" {
			continue
		}
		n, err := strconv.ParseInt(id, 16, 64)
		state, known := hrState[f.text("state")]
		if err != nil || !known || f.text("requested_by") == "human" {
			continue
		}
		p := proposal{flat: f, wireID: n, state: state}
		p.created, _ = f.get("created").(int64)
		p.settled = p.created
		for _, k := range []string{"decided", "applied"} { // applied wins, as card()'s `at` has it
			if t, _ := f.get(k).(int64); t != 0 {
				p.settled = t
			}
		}
		out = append(out, p)
	}
	return out
}

func (e *Env) proposalView(p proposal) map[string]any {
	agent := p.text("agent")
	if p.text("kind") == "bundle" {
		agent = fmt.Sprintf("%d changes", len(p.items()))
	}
	v := map[string]any{"id": p.wireID, "kind": p.text("kind"), "agent": agent, "title": p.title(), "diff": p.diff(),
		"subject": p.subject(), "state": p.state, "created": ts(p.created), "settled": nil, "error": nil}
	if p.state != "open" {
		v["settled"] = ts(p.settled)
	}
	if p.state == "apply_failed" && p.truthy("error") {
		v["error"] = cut(p.text("error"), maxError)
	}
	if p.truthy("requested_by") {
		v["requested_by"] = p.text("requested_by")
	}
	if p.truthy("why") {
		v["reason"] = p.text("why")
	}
	asked := map[string]any{}
	for _, k := range []string{"vendor", "model", "effort", "team", "repo", "to"} {
		if p.truthy(k) {
			asked[k] = p.text(k)
		}
	}
	if keys, vals := p.sets(); len(keys) > 0 && p.text("kind") == "settings" {
		sets := map[string]any{}
		for _, k := range keys {
			sets[k] = vals[k]
		}
		asked["sets"] = sets
	}
	if len(asked) > 0 && p.text("kind") != "bundle" {
		v["asked"] = asked
	}
	return v
}

// hrList is hr.list: what waits for the founder, oldest first, then what settled in the last 7 days (at most 50), newest first.
// With a state, only that state in the same order, with neither bound.
func (e *Env) hrList(_ *rpc.Conn, params map[string]any) (any, error) {
	var only string
	if v, has := params["state"]; has {
		s, ok := v.(string)
		if !ok {
			return nil, badParam("state is text", "state")
		}
		only = s
	}
	var waiting, settled []proposal
	for _, p := range e.proposals() {
		if only != "" && p.state != only {
			continue
		}
		if p.waits() {
			waiting = append(waiting, p)
		} else {
			settled = append(settled, p)
		}
	}
	sort.SliceStable(waiting, func(i, j int) bool {
		a, b := waiting[i], waiting[j]
		return a.created < b.created || (a.created == b.created && a.wireID < b.wireID)
	})
	sort.SliceStable(settled, func(i, j int) bool {
		a, b := settled[i], settled[j]
		return a.settled > b.settled || (a.settled == b.settled && a.wireID > b.wireID)
	})
	if only == "" {
		since := time.Now().Add(-settledWindow).Unix()
		kept := settled[:0]
		for _, p := range settled {
			if p.settled >= since && len(kept) < settledMax {
				kept = append(kept, p)
			}
		}
		settled = kept
	}
	out := []any{}
	for _, p := range append(waiting, settled...) {
		out = append(out, e.proposalView(p))
	}
	return map[string]any{"proposals": out}, nil
}

// cut is s at most n characters long.
func cut(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

// HRView is the Proposal hr.list shows for the stored proposal with this id, nil for one it never lists (a human's own change, a state the
// wire has no word for).
func (e *Env) HRView(pid string) map[string]any {
	for _, p := range e.proposals() {
		if p.text("id") == pid {
			return e.proposalView(p)
		}
	}
	return nil
}

// HRProposals is every proposal hr.list can show, as it shows it, in no bound and by wire id: what a change in the store is told from.
func (e *Env) HRProposals() []map[string]any {
	ps := e.proposals()
	sort.SliceStable(ps, func(i, j int) bool { return ps[i].wireID < ps[j].wireID })
	out := make([]map[string]any, 0, len(ps))
	seen := map[int64]int{}
	for _, p := range ps {
		v := e.proposalView(p)
		if at, dup := seen[p.wireID]; dup { // two files with one id: the later one stands, as a dict keyed by id has it
			out[at] = v
			continue
		}
		seen[p.wireID] = len(out)
		out = append(out, v)
	}
	return out
}
