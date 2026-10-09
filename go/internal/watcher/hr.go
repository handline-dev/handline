package watcher

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/stream"
)

// HR is the hr topic: a proposal raised (hr.raised: it appeared open), and one that left open (decided, withdrawn) or whose apply ran
// again (hr.settled). The store is files, so the watcher's change check also looks at HRSignature. Its first look says nothing.
type HR struct {
	Reads *reads.Env
	seen  map[int64]string
}

func (*HR) Name() string { return "hr" }

// HRSignature is whether the store changed: the proposal files by name and modification time.
func HRSignature(home string) func() string {
	return func() string {
		files, _ := filepath.Glob(filepath.Join(home, "hr", "*.yaml"))
		slices.Sort(files)
		var b strings.Builder
		for _, f := range files {
			if st, err := os.Stat(f); err == nil {
				fmt.Fprintf(&b, "%s=%d;", filepath.Base(f), st.ModTime().UnixNano())
			}
		}
		return b.String()
	}
}

// key is what a change in a proposal is told from: its state, when it settled and its error.
func key(w map[string]any) string { return fmt.Sprint(w["state"], "|", w["settled"], "|", w["error"]) }

func (h *HR) Scan(_ *sql.DB, emit Emit) error {
	now := map[int64]map[string]any{}
	ids := []int64{}
	for _, w := range h.Reads.HRProposals() {
		id := w["id"].(int64)
		now[id] = w
		ids = append(ids, id)
	}
	before := h.seen
	h.seen = map[int64]string{}
	for id, w := range now {
		h.seen[id] = key(w)
	}
	if before == nil {
		return nil
	}
	slices.Sort(ids)
	for _, id := range ids {
		w := now[id]
		was, had := before[id]
		if had && was == key(w) {
			continue
		}
		var typ string
		switch {
		case !had && w["state"] == "open":
			typ = "hr.raised"
		case w["state"] == "open":
			continue
		default:
			typ = "hr.settled"
		}
		agent := ""
		if w["kind"] != "bundle" {
			agent, _ = w["agent"].(string)
		}
		title, _ := w["title"].(string)
		if _, err := emit(stream.Entry{Topic: "hr", Type: typ, Data: map[string]any{"proposal": w}, Agent: agent, Title: title,
			Target: map[string]any{"screen": "hr", "id": fmt.Sprint(id)}}); err != nil {
			return err
		}
	}
	return nil
}
