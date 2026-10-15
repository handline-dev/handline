package attachments

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
)

// The images of a message (contract x-attachments, texts): those its text names, in order, then those it was sent with (chat.send
// attachments) that its text does not name, each once.

// CheckMessage checks and claims what a message may carry: the images text (field) names, as for a task's text (Check), and listed, the
// ids sent with it, at most MaxPerMessage together. It answers the Attachments of the listed images its text does not name, to Record.
func (s *Store) CheckMessage(q db.Q, text, field string, listed []string, agent bool) ([]Info, error) {
	named := map[string]bool{}
	for _, r := range Refs(text) {
		named[r.ID] = true
	}
	var extra []string
	for _, id := range listed {
		if !named[id] {
			named[id] = true
			extra = append(extra, id)
		}
	}
	if len(extra) > 0 && len(named) > MaxPerMessage {
		return nil, badText(fmt.Sprintf("the message carries %d images: at most %d", len(named), MaxPerMessage), "attachments")
	}
	ids, err := s.Check(q, text, "", field, agent)
	if err != nil {
		return nil, err
	}
	for _, id := range extra {
		if err := s.checkID(id, "attachments"); err != nil {
			return nil, err
		}
	}
	infos, err := s.Claim(append(ids, extra...))
	if err != nil {
		return nil, err
	}
	return infos[len(ids):], nil
}

// sentWith is the images message rid was sent with that text does not name.
func sentWith(q db.Q, rid int64, text string) []Info {
	named := map[string]bool{}
	for _, r := range Refs(text) {
		named[r.ID] = true
	}
	infos, _ := Of(q, rid)
	var out []Info
	for _, in := range infos {
		if !named[in.ID] {
			out = append(out, in)
		}
	}
	return out
}

// MessageParts is ChatMessage.parts: text cut at the images it names (Parts), then one part per image it was sent with; nil when the
// message has no image.
func (s *Store) MessageParts(q db.Q, rid int64, text string, withPath bool) []any {
	extra := sentWith(q, rid, text)
	parts := s.Parts(text, withPath)
	if len(extra) == 0 {
		return parts
	}
	if parts == nil && text != "" {
		parts = []any{map[string]any{"text": text}}
	}
	for _, in := range extra {
		parts = append(parts, s.imagePart("", in.ID, withPath))
	}
	return parts
}

// MessageMaps is ChatMessage.attachments: every image of the message, as Attachments; an image its text names that is no longer kept is
// left out (its type and size are gone). nil for none.
func (s *Store) MessageMaps(q db.Q, rid int64, text string) []any {
	var out []any
	seen := map[string]bool{}
	for _, r := range Refs(text) {
		if !seen[r.ID] {
			seen[r.ID] = true
			if in, err := s.Lookup(r.ID); err == nil {
				out = append(out, in.Map())
			}
		}
	}
	for _, in := range sentWith(q, rid, text) {
		out = append(out, in.Map())
	}
	return out
}

// MessageLines is what an agent is told after message rid: one path line per image of the message, then per image the texts in more name,
// each once and numbered in that order. An image no longer kept is left out. "" for none.
func (s *Store) MessageLines(q db.Q, rid int64, text string, more ...string) string {
	var ids []string
	for _, r := range Refs(text) {
		ids = append(ids, r.ID)
	}
	for _, in := range sentWith(q, rid, text) {
		ids = append(ids, in.ID)
	}
	for _, t := range more {
		for _, r := range Refs(t) {
			ids = append(ids, r.ID)
		}
	}
	var lines []string
	seen := map[string]bool{}
	for _, id := range ids {
		if seen[id] {
			continue
		}
		seen[id] = true
		if in, err := s.Lookup(id); err == nil {
			lines = append(lines, s.line(len(lines)+1, in))
		}
	}
	return strings.Join(lines, "\n")
}
