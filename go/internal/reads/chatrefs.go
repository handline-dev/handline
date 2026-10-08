package reads

import (
	"strings"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// chat.refs follows handline/daemon/rpc_uireads.py: what a `#` in the composer can point at, the agent's newest requests sent or
// received that were not withdrawn or turned away.

const (
	refsShown = 30
	refLine   = 60
)

func (e *Env) chatRefs(c *rpc.Conn, params map[string]any) (any, error) {
	name, err := e.chatAgent(params, true)
	if err != nil {
		return nil, err
	}
	rows, err := query(e.DB, "SELECT id, requester, target, body FROM requests WHERE state NOT IN "+neverReached+
		" AND (requester = ? OR target = ?) ORDER BY id DESC LIMIT ?", name, name, refsShown)
	if err != nil {
		return nil, err
	}
	out := []any{}
	for _, r := range rows {
		first, _, _ := strings.Cut(strings.TrimSpace(r.str("body")), "\n")
		out = append(out, map[string]any{"request": r.i("id"), "from": r.str("requester"), "to": r.str("target"),
			"line": firstRunes(textsafe.Oneline(first), refLine)})
	}
	return map[string]any{"requests": out}, nil
}

func firstRunes(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}
