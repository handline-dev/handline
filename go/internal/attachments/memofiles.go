package attachments

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/db"
)

// A memo's files are copied into the store and linked by id, so they never sit in a worktree.

const Markdown = "text/markdown"

var byExt = map[string]string{".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".md": Markdown}

func ext(mime string) string {
	if mime == Markdown {
		return "md"
	}
	return kinds[mime]
}

// extOf is the ending a file is stored with: its type's for an image or Markdown, else the one finish chose for it (Info.Ending).
func extOf(i Info) string {
	if e := ext(i.Mime); e != "" {
		return e
	}
	if i.Ending != "" {
		return i.Ending
	}
	return "bin"
}

// endingFor is the ending a file of another type than an image or Markdown is stored with: the one its name ends in when that is letters
// and digits and at most 8 long, else bin. A tool picks image and PDF handling by ending, so an image ending is never taken from a name
// (only an image's bytes are checked as an image), and pdf only when the bytes begin as a PDF does.
func endingFor(name string, head []byte) string {
	e := strings.ToLower(strings.TrimPrefix(filepath.Ext(name), "."))
	switch {
	case e == "" || len(e) > 8 || strings.Trim(e, "abcdefghijklmnopqrstuvwxyz0123456789") != "":
		return "bin"
	case e == "png" || e == "jpg" || e == "jpeg" || e == "gif" || e == "webp":
		return "bin"
	case e == "pdf" && !bytes.HasPrefix(head, []byte("%PDF-")):
		return "bin"
	}
	return e
}

// FileMime checks an image by its first bytes, not only its ending.
func FileMime(name string, b []byte) (string, error) {
	mime, ok := byExt[strings.ToLower(filepath.Ext(name))]
	switch {
	case !ok:
		return "", badParam(name+" is not a PNG, JPEG, GIF, WebP or .md file", "files")
	case mime == Markdown:
		if !utf8.Valid(b) || bytes.IndexByte(b, 0) >= 0 {
			return "", badParam(name+" is not text", "files")
		}
	case Sniff(b) != mime:
		return "", badParam(name+" is not "+mime+" by its first bytes", "files")
	}
	return mime, nil
}

// Add keeps b unclaimed, so it is swept after UnsentTTL unless a memo links it.
func (s *Store) Add(name, mime string, b []byte) (Info, error) {
	if err := s.ensure(); err != nil {
		return Info{}, err
	}
	im := stored{Info: Info{ID: newID(), Name: FileName(name), Mime: mime, Bytes: int64(len(b))}, Created: s.now().Unix()}
	if err := os.WriteFile(s.Path(im.Info), b, 0o600); err != nil {
		return Info{}, err
	}
	if err := writeJSON(s.sub("meta", im.ID+".json"), im); err != nil {
		os.Remove(s.Path(im.Info))
		return Info{}, err
	}
	return im.Info, nil
}

func FilePaths(p map[string]any) ([]string, error) {
	v, ok := p["files"]
	if !ok || v == nil {
		return nil, nil
	}
	list, ok := v.([]any)
	if !ok || len(list) > MaxPerMessage {
		return nil, badParam(fmt.Sprintf("files is a list of at most %d absolute file paths", MaxPerMessage), "files")
	}
	paths := make([]string, len(list))
	for i, x := range list {
		if paths[i], ok = x.(string); !ok {
			return nil, badParam(fmt.Sprintf("files is a list of at most %d absolute file paths", MaxPerMessage), "files")
		}
	}
	return paths, nil
}

// AddFiles reads and keeps the files at paths; one never linked is swept like an unsent upload.
func (s *Store) AddFiles(q db.Q, home, by string, founder bool, paths []string) ([]Info, error) {
	if len(paths) == 0 {
		return nil, nil
	}
	roots, err := Roots(q, home, by, founder)
	if err != nil {
		return nil, err
	}
	out := make([]Info, 0, len(paths))
	for _, path := range paths {
		b, err := ReadUnder(path, roots, MaxImage, "file", "files")
		if err != nil {
			return nil, err
		}
		mime, err := FileMime(path, b)
		if err != nil {
			return nil, err
		}
		info, err := s.Add(filepath.Base(path), mime, b)
		if err != nil {
			return nil, err
		}
		out = append(out, info)
	}
	return out, nil
}

// AddImages is AddFiles for a message: only images, which a chat shows.
func (s *Store) AddImages(q db.Q, home, by string, founder bool, paths []string) ([]Info, error) {
	for _, p := range paths {
		if strings.EqualFold(filepath.Ext(p), ".md") {
			return nil, badParam(p+" is not an image: a message carries PNG, JPEG, GIF or WebP files", "files")
		}
	}
	return s.AddFiles(q, home, by, founder, paths)
}

// Carry records files as the images of message rid and claims them.
func (s *Store) Carry(q db.Q, rid int64, files []Info) error {
	return s.keep(files, func() error { return Record(q, rid, files) })
}

func (s *Store) AttachMemo(q db.Q, mid int64, files []Info) error {
	return s.keep(files, func() error { return LinkMemo(q, mid, files) })
}

// keep links files with link, then claims them; no files is nothing to do.
func (s *Store) keep(files []Info, link func() error) error {
	if len(files) == 0 {
		return nil
	}
	if err := link(); err != nil {
		return err
	}
	ids := make([]string, len(files))
	for i, f := range files {
		ids[i] = f.ID
	}
	_, err := s.Claim(ids)
	return err
}

func memoKey(mid int64) string { return fmt.Sprintf("memo-files:%d", mid) }

func LinkMemo(q db.Q, mid int64, infos []Info) error {
	b, err := json.Marshal(infos)
	if err != nil {
		return err
	}
	return db.SetMeta(q, memoKey(mid), string(b))
}

// UnlinkMemo forgets the files of a deleted memo: they are kept KeepFor from their claim, then swept.
func UnlinkMemo(q db.Q, mid int64) error {
	_, err := q.Exec("DELETE FROM meta WHERE name = ?", memoKey(mid))
	return err
}

func MemoFiles(q db.Q, mid int64) ([]Info, error) {
	v, ok, err := db.Meta(q, memoKey(mid))
	if err != nil || !ok {
		return nil, err
	}
	var infos []Info
	if json.Unmarshal([]byte(v), &infos) != nil {
		return nil, nil
	}
	return infos, nil
}

const memoFilesHeld = `SELECT tm.task_id AS tid, m.value AS text FROM meta m JOIN task_memos tm ON m.name = 'memo-files:' || tm.id`

// FileMap gives the path only withPath, since only an agent reads files by path.
func (s *Store) FileMap(in Info, withPath bool) map[string]any {
	kind := "image"
	if in.Mime == Markdown {
		kind = "markdown"
	}
	m := map[string]any{"id": in.ID, "kind": kind, "name": in.Name, "mime": in.Mime, "bytes": in.Bytes}
	if _, err := s.Lookup(in.ID); err != nil {
		m["gone"] = true
		if s.Removed(in.ID) {
			m["removed"] = true
		}
	} else if withPath {
		m["path"] = s.Path(in)
	}
	return m
}

// FileLines leaves out a file no longer kept.
func (s *Store) FileLines(infos []Info) string {
	var lines []string
	for _, in := range infos {
		if _, err := s.Lookup(in.ID); err == nil {
			lines = append(lines, FileLine(len(lines)+1, s.Path(in), in.Mime, in.Bytes))
		}
	}
	return strings.Join(lines, "\n")
}

// FileLine takes where as a name for a reader not given the path.
func FileLine(n int, where, mime string, bytes int64) string {
	return fmt.Sprintf("[file %d: %s (%s, %s)]", n, where, mime, size(bytes))
}

var fileLine = regexp.MustCompile(`\A\[file \d+: .*\]\z`)

// SplitFileLines takes the FileLines off the end of a text told with them.
func SplitFileLines(told string) (text string, files []string) {
	lines := strings.Split(told, "\n")
	n := len(lines)
	for n > 1 && fileLine.MatchString(lines[n-1]) {
		n--
	}
	return strings.Join(lines[:n], "\n"), lines[n:]
}
