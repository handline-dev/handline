package chatwrites

import (
	"database/sql"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/taskwrites"
	"github.com/handline/handline/go/internal/team"
)

// trust.propose, trust.list and trust.remove (handline/daemon/rpc_trust.py): `hl trust add`, `list`, `proposals` and `remove` over the
// store (team.Trust), with what the command prints as said. An agent's propose raises a decision for the founder, in the transaction of the
// call's record; the founder's declares the root, and a remove drops one, in the protected store, so those two leave an intent as answer.add
// does.

func (w *writes) registerTrust(s *rpc.Server) {
	s.Register("trust.propose", rpc.Method{Role: "agent", Write: true, Handler: w.trustPropose})
	s.Register("trust.list", rpc.Method{Role: "agent", Handler: w.trustList})
	s.Register("trust.remove", rpc.Method{Role: "founder", Write: true, Handler: w.trustRemove})
}

// trustRoot is a root the caller spelled out in full: the store would resolve a relative one, or a `~`, against the daemon's own folder and home.
func trustRoot(p map[string]any) (string, error) {
	root, ok := p["root"].(string)
	if !ok || strings.TrimSpace(root) == "" {
		return "", badParam("root is text", "root")
	}
	if !filepath.IsAbs(root) {
		return "", badParam("root is an absolute path; the client resolves it against its own folder", "root")
	}
	return root, nil
}

func (w *writes) trustPropose(c *rpc.Conn, p map[string]any) (any, error) {
	root, err := trustRoot(p)
	if err != nil {
		return nil, err
	}
	why := ""
	if v := p["why"]; v != nil {
		s, ok := v.(string)
		if !ok {
			return nil, badParam("why is text", "why")
		}
		why = s
	}
	if c.Caller.Role != "founder" && c.Caller.Role != "agent" {
		return nil, rpc.Refused("a trust root is proposed by an agent or declared by the founder, not from a device", "role", "")
	}
	if c.Caller.Role == "founder" {
		if why == "" {
			why = "added by the founder"
		}
		key := w.intent(c, p, "trust.propose")
		retry, err := w.begun(key)
		if err != nil {
			return nil, err
		}
		t := team.NewTrust(w.e.Home)
		ok, said := t.Enact(root, why)
		if !ok && retry && t.IsDeclared(root) { // judged by the store, never by the words of the refusal
			ok, said = true, root+" is now a declared root"
		}
		if !ok {
			return nil, w.failed(key, rpc.Refused(said, "", ""))
		}
		return w.finish(c, key, said)
	}
	if w.tell == nil {
		return nil, fmt.Errorf("a trust proposal needs the task teller to raise its decision")
	}
	me := c.Caller.Actor()
	var asked *taskwrites.Asked
	out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		raise := func(title, text string) (bool, string, int64, int64, error) {
			var tid int64
			var err error
			tid, asked, err = taskwrites.OpenTask(tx, w.tell, me, taskwrites.NewIn{Owner: db.Human, Title: title, Body: text,
				Prio: db.DefaultPriority, Expects: "decision"})
			if err != nil {
				return false, "", 0, 0, err
			}
			return asked.OK, asked.Msg, asked.RID, tid, nil
		}
		ok, said, id, err := team.NewTrust(w.e.Home).Propose(root, why, me, raise)
		if err != nil {
			return nil, err
		}
		if !ok {
			if asked != nil { // the refused ask's writes stand, as a refused task.new's do
				return nil, &rpc.CommitThen{Err: rpc.Refused(said, "", "")}
			}
			return nil, rpc.Refused(said, "", "")
		}
		return map[string]any{"accepted": true, "said": said, "proposal": id}, nil
	})
	if asked != nil && asked.After != nil && (err == nil || !asked.OK) { // what follows the writes that stood
		guarded(w.e, "telling after a trust proposal", func() (string, error) { return asked.After(), nil })
	}
	return out, err
}

func (w *writes) trustList(_ *rpc.Conn, p map[string]any) (any, error) {
	wanted := false
	if v := p["proposals"]; v != nil {
		b, ok := v.(bool)
		if !ok {
			return nil, badParam("proposals is true or false", "proposals")
		}
		wanted = b
	}
	t := team.NewTrust(w.e.Home)
	roots, declared := []any{}, []any{}
	for _, d := range t.Declared() {
		roots = append(roots, d.Root)
		declared = append(declared, map[string]any{"root": d.Root, "why": d.Why, "added_by": d.AddedBy})
	}
	proposals := []any{}
	if wanted {
		list, err := t.Listing()
		if err != nil {
			return nil, err
		}
		for _, prop := range list {
			proposals = append(proposals, proposalRow(prop))
		}
	}
	return map[string]any{"roots": roots, "declared": declared, "proposals": proposals}, nil
}

// proposalRow is a proposal as trust.list shows it: request only when the file has one, requested_by only when it is set.
func proposalRow(prop team.Proposal) map[string]any {
	str := func(k, missing string) string {
		v := prop.Get(k)
		if v == nil || v == "" {
			return missing
		}
		return fmt.Sprint(v)
	}
	row := map[string]any{"id": str("id", ""), "root": str("root", ""), "state": str("state", "?")}
	if v := prop.Get("request"); v != nil {
		row["request"] = v
	}
	if by := str("requested_by", ""); by != "" {
		row["requested_by"] = by
	}
	return row
}

func (w *writes) trustRemove(c *rpc.Conn, p map[string]any) (any, error) {
	root, err := trustRoot(p)
	if err != nil {
		return nil, err
	}
	key := w.intent(c, p, "trust.remove")
	retry, err := w.begun(key)
	if err != nil {
		return nil, err
	}
	ok, said := team.NewTrust(w.e.Home).Remove(root)
	if !ok && retry && !w.held(root) {
		ok, said = true, root+" is no longer a declared root" // judged by the store: the root is not there
	}
	if !ok {
		if strings.Contains(said, "is not a declared root") {
			return nil, w.failed(key, notFound("root", root, said))
		}
		return nil, w.failed(key, rpc.Refused(said, "", ""))
	}
	return w.finish(c, key, said)
}
