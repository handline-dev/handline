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
		out = append(out, s.imagePart(r.Caption, r.ID, withPath))
	}
	if at < len(text) {
		out = append(out, map[string]any{"text": text[at:]})
	}
	return out
}

// imagePart is the ImagePart of image id: gone when it is no longer kept, with its path for an agent (withPath).
func (s *Store) imagePart(caption, id string, withPath bool) map[string]any {
	part := map[string]any{"caption": caption}
	info, err := s.Lookup(id)
	if err != nil {
		part["image"], part["gone"] = map[string]any{"id": id}, true
		return part
	}
	part["image"] = info.Map()
	if withPath {
		part["path"] = s.Path(info)
	}
	return part
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
