package reads

import (
	"encoding/json"
	"os"
	"path/filepath"

	"github.com/handline/handline/go/internal/authority"
	"github.com/handline/handline/go/internal/rpc"
)

// answersList is answers.list: the folders trusted for every session and the rules that answer a vendor's dialog, kept in
// the protected store (handline/daemon/rpc_answers.py, actions/dialogs.standing_answers). The founder's alone.
func (e *Env) answersList(c *rpc.Conn, params map[string]any) (any, error) {
	store := authority.Root(e.Home)
	out := []any{}
	for _, r := range storedList(filepath.Join(store, "trust", "roots.json")) {
		if root, ok := r["root"].(string); ok {
			out = append(out, map[string]any{"kind": "trust", "key": []any{root}, "text": "trust " + root})
		}
	}
rules:
	for _, r := range storedList(filepath.Join(store, "dialog_rules", "rules.json")) {
		var key [3]string
		for i, k := range []string{"vendor", "title", "label"} {
			s, ok := r[k].(string)
			if !ok || s == "" {
				continue rules
			}
			key[i] = s
		}
		out = append(out, map[string]any{"kind": "dialog", "key": []any{key[0], key[1], key[2]},
			"text": key[0] + ": \"" + key[1] + "\" -> " + key[2]})
	}
	return map[string]any{"answers": out}, nil
}

// storedList is the objects of a JSON list kept in a file; none when it is missing, unreadable or not a list.
func storedList(path string) []map[string]any {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var rows []any
	if json.Unmarshal(b, &rows) != nil {
		return nil
	}
	var out []map[string]any
	for _, r := range rows {
		if m, ok := r.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}
