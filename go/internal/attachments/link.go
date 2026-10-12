package attachments

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
)

func linkKey(rid int64) string { return fmt.Sprintf("attachments:%d", rid) }

// Record keeps which images the message rid carries, in order.
func Record(q db.Q, rid int64, infos []Info) error {
	b, err := json.Marshal(infos)
	if err != nil {
		return err
	}
	return db.SetMeta(q, linkKey(rid), string(b))
}

// Of is the images message rid carries; none for a message with none.
func Of(q db.Q, rid int64) ([]Info, error) {
	v, ok, err := db.Meta(q, linkKey(rid))
	if err != nil || !ok {
		return nil, err
	}
	var infos []Info
	if err := json.Unmarshal([]byte(v), &infos); err != nil {
		return nil, nil
	}
	return infos, nil
}

// Maps is Of as the members of ChatMessage.attachments; nil for none.
func Maps(q db.Q, rid int64) ([]any, error) {
	infos, err := Of(q, rid)
	if err != nil || len(infos) == 0 {
		return nil, err
	}
	out := make([]any, len(infos))
	for i, in := range infos {
		out[i] = in.Map()
	}
	return out, nil
}

// size is a file's size as the path line says it.
func size(n int64) string {
	switch {
	case n < 1024:
		return fmt.Sprintf("%d B", n)
	case n < 1<<20:
		return fmt.Sprintf("%d KB", (n+512)/1024)
	}
	return fmt.Sprintf("%.1f MB", float64(n)/(1<<20))
}

// Lines is what the agent is told of the images of message rid: one line `[image N: /path (type, size)]` each. An image the store no longer holds is left out. "" for a message with none.
func (s *Store) Lines(q db.Q, rid int64) string {
	infos, _ := Of(q, rid)
	var lines []string
	for i, in := range infos {
		if _, err := s.Lookup(in.ID); err != nil {
			continue
		}
		lines = append(lines, fmt.Sprintf("[image %d: %s (%s, %s)]", i+1, s.Path(in), in.Mime, size(in.Bytes)))
	}
	return strings.Join(lines, "\n")
}
