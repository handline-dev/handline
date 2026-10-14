package attachments

import "fmt"

// Parts is text cut at the images it names (contract TextPart, ImagePart), or nil when it names none. An image no longer kept is gone: its
// caption and id without a type or size. withPath adds where an image is stored, for an agent, which reads it by path.
func (s *Store) Parts(text string, withPath bool) []any {
	refs := Refs(text)
	if len(refs) == 0 {
		return nil
	}
	var out []any
	at := 0
	for _, r := range refs {
		if r.Start > at {
			out = append(out, map[string]any{"text": text[at:r.Start]})
		}
		at = r.End
		part := map[string]any{"caption": r.Caption}
		if info, err := s.Lookup(r.ID); err != nil {
			part["image"], part["gone"] = map[string]any{"id": r.ID}, true
		} else {
			part["image"] = info.Map()
			if withPath {
				part["path"] = s.Path(info)
			}
		}
		out = append(out, part)
	}
	if at < len(text) {
		out = append(out, map[string]any{"text": text[at:]})
	}
	return out
}

// RefLines is the path lines of the images texts name, each once, numbered on from after: what an agent is told after a brief, a memo or
// a reject note. An image no longer kept is left out.
func (s *Store) RefLines(after int, texts ...string) []string {
	var lines []string
	seen := map[string]bool{}
	for _, text := range texts {
		for _, r := range Refs(text) {
			if seen[r.ID] {
				continue
			}
			seen[r.ID] = true
			if info, err := s.Lookup(r.ID); err == nil {
				lines = append(lines, s.line(after+len(lines)+1, info))
			}
		}
	}
	return lines
}

func (s *Store) line(n int, in Info) string { return Line(n, s.Path(in), in.Mime, in.Bytes) }

// Line is how an agent is told of image n: `[image N: /path (type, size)]`.
func Line(n int, path, mime string, bytes int64) string {
	return fmt.Sprintf("[image %d: %s (%s, %s)]", n, path, mime, size(bytes))
}

// KeepRefs is cut, a prefix of whole, without a reference it cuts in two and with every reference it lost put back after it, so a
// shortened line still names every image.
func KeepRefs(cut, whole string) string {
	refs := Refs(whole)
	for _, r := range refs {
		if r.Start < len(cut) && len(cut) < r.End {
			cut = cut[:r.Start]
		}
	}
	kept := len(cut)
	for _, r := range refs {
		if r.End > kept {
			cut += " " + whole[r.Start:r.End]
		}
	}
	return cut
}
