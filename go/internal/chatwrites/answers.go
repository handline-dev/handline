package chatwrites

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
)

// answer.add and answer.remove (handline/daemon/rpc_answers.py): the founder's standing answers, kept in the protected store.
//
// The stores are files, so an effect and its write_effects record cannot be one transaction. A retry is told apart instead: the call leaves
// an intent (a cursor named for its caller and request_id) before it writes and clears it once it has recorded its answer, so a retry that
// finds the intent knows its first attempt may have got as far as the write, and takes "already so" for the outcome it wanted. A first attempt
// is judged strictly.

func (w *writes) registerAnswers(s *rpc.Server) {
	s.Register("answer.add", rpc.Method{Role: "founder", Write: true, Handler: w.answerAdd})
	s.Register("answer.remove", rpc.Method{Role: "founder", Write: true, Handler: w.answerRemove})
}

func (w *writes) intent(c *rpc.Conn, p map[string]any, name string) string {
	return fmt.Sprintf("intent:%s:%s:%v", name, c.Caller.Writer(), p["request_id"])
}

// begun leaves the intent; true when it was already there (a retry of a call that did not finish).
func (w *writes) begun(key string) (bool, error) {
	_, again, err := db.CursorUpdated(w.e.DB, key)
	if err != nil {
		return false, err
	}
	return again, db.SetCursor(w.e.DB, key, 1)
}

// finish ends the call: the answer is recorded with the intent's removal, which is dropped either way.
func (w *writes) finish(c *rpc.Conn, key, said string) (any, error) {
	return c.Effect(func(tx *sql.Tx) (map[string]any, error) {
		if err := db.ClearCursor(tx, key); err != nil {
			return nil, err
		}
		return map[string]any{"accepted": true, "said": said}, nil
	})
}

func (w *writes) failed(key string, err error) error {
	if cerr := db.ClearCursor(w.e.DB, key); cerr != nil {
		return cerr
	}
	return err
}

// pyRepr is a string as Python's repr writes the common case.
func pyRepr(s string) string {
	if strings.ContainsAny(s, "'\\\n\r\t") {
		return fmt.Sprintf("%q", s)
	}
	return "'" + s + "'"
}

// held says whether the standing answer of this kind and key is saved now.
func (w *writes) held(kind string, key []string) bool {
	if kind == "trust" {
		for _, r := range team.NewTrust(w.e.Home).ListRoots() {
			if len(key) == 1 && r == key[0] {
				return true
			}
		}
		return false
	}
	for _, r := range team.NewDialogRules(w.e.Home).List() {
		if len(key) == 3 && r.Vendor == key[0] && r.Title == key[1] && r.Label == key[2] {
			return true
		}
	}
	return false
}

func (w *writes) answerAdd(c *rpc.Conn, p map[string]any) (any, error) {
	given := map[string]string{}
	any_ := false
	for _, k := range []string{"folder", "vendor", "title", "label"} {
		v := p[k]
		if v != nil {
			if _, ok := v.(string); !ok {
				return nil, badParam(k+" is text", k)
			}
		}
		s, _ := v.(string)
		given[k] = strings.TrimSpace(s)
		any_ = any_ || given[k] != ""
	}
	if !any_ {
		return nil, badParam("give a folder to trust, or the vendor, title and label of a rule", "folder")
	}
	key := w.intent(c, p, "answer.add")
	retry, err := w.begun(key)
	if err != nil {
		return nil, err
	}
	ok, said := w.addAnswer(given)
	if !ok && retry {
		// judged by the store, never by the words of the refusal: what was asked is so only if it is there exactly
		var so bool
		var what string
		if given["folder"] != "" {
			so, what = team.NewTrust(w.e.Home).IsDeclared(given["folder"]), given["folder"]
		} else {
			so = w.held("dialog", []string{given["vendor"], given["title"], given["label"]})
			what = strings.Join([]string{given["vendor"], given["title"], given["label"]}, " / ")
		}
		if so {
			ok, said = true, what+" is already saved"
		}
	}
	if !ok {
		return nil, w.failed(key, rpc.Refused(said, "", ""))
	}
	return w.finish(c, key, said)
}

func (w *writes) addAnswer(given map[string]string) (bool, string) {
	if folder := given["folder"]; folder != "" {
		if textsafe.HasUnsafe(folder) || !isDir(folder) {
			return false, fmt.Sprintf("trust %s: not a folder", pyRepr(folder))
		}
		return team.NewTrust(w.e.Home).Enact(folder, "added in the Home settings")
	}
	return team.NewDialogRules(w.e.Home).Add(given["vendor"], given["title"], given["label"])
}

func isDir(path string) bool {
	if path == "~" || strings.HasPrefix(path, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			path = filepath.Join(home, path[1:])
		}
	}
	st, err := os.Stat(path)
	return err == nil && st.IsDir()
}

func (w *writes) answerRemove(c *rpc.Conn, p map[string]any) (any, error) {
	kind, _ := p["kind"].(string)
	if kind != "trust" && kind != "dialog" {
		return nil, badParam("kind is trust or dialog", "kind")
	}
	list, _ := p["key"].([]any)
	want := 3
	if kind == "trust" {
		want = 1
	}
	var key []string
	for _, k := range list {
		if s, ok := k.(string); ok && s != "" {
			key = append(key, s)
		}
	}
	if len(key) != len(list) || len(key) != want {
		return nil, badParam("key is [folder] for trust and [vendor, title, label] for dialog", "key")
	}
	ik := w.intent(c, p, "answer.remove")
	retry, err := w.begun(ik)
	if err != nil {
		return nil, err
	}
	var ok bool
	var said string
	if kind == "trust" {
		ok, said = team.NewTrust(w.e.Home).Remove(key[0])
	} else {
		ok, said = team.NewDialogRules(w.e.Home).Remove(key[0], key[1], key[2])
	}
	absent := !w.held(kind, key)
	gone := !ok && absent && (strings.Contains(said, "no rule") || strings.Contains(said, "not a declared root"))
	if !ok && retry && absent {
		ok, said = true, strings.Join(key, " / ")+" is already removed" // judged by the store: the key is not there
	}
	if !ok {
		if gone {
			e := notFound("answer", strings.Join(key, " / "), said)
			return nil, w.failed(ik, e)
		}
		return nil, w.failed(ik, rpc.Refused(said, "", ""))
	}
	return w.finish(c, ik, said)
}
