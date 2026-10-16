package attachments

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/db"
)

// The files a memo carries (task.memo.add files): images and Markdown its writer names by path. The daemon reads each, keeps it in the store
// like an uploaded image, and links it to the memo by id, so the files never sit in a worktree.

// Markdown is the type a memo's .md file is kept as.
const Markdown = "text/markdown"

// byExt is the type each file name ending takes; nothing else is kept.
var byExt = map[string]string{".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".md": Markdown}

// ext is the extension a file of mime is stored with.
func ext(mime string) string {
	if mime == Markdown {
		return "md"
	}
	return kinds[mime]
}

// FileMime is the type the file named name, holding b, is kept as: an image whose first bytes match its ending, or Markdown text.
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

// Add keeps b as a finished file that nothing names yet: like an upload, it goes after UnsentTTL unless a memo links it.
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

func memoKey(mid int64) string { return fmt.Sprintf("memo-files:%d", mid) }

// LinkMemo records the files memo mid carries, in order.
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

// MemoFiles is the files memo mid carries; none for a memo with none.
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

// memoFilesHeld is the files of every memo, with its task, for Held.
const memoFilesHeld = `SELECT tm.task_id AS tid, m.value AS text FROM meta m JOIN task_memos tm ON m.name = 'memo-files:' || tm.id`

// FileMap is the MemoFile of the contract: an image or Markdown, read with attachment.get; gone when it is no longer kept. withPath adds
// where it is stored, for an agent, which reads it by path.
func (s *Store) FileMap(in Info, withPath bool) map[string]any {
	kind := "image"
	if in.Mime == Markdown {
		kind = "markdown"
	}
	m := map[string]any{"id": in.ID, "kind": kind, "name": in.Name, "mime": in.Mime, "bytes": in.Bytes}
	if _, err := s.Lookup(in.ID); err != nil {
		m["gone"] = true
	} else if withPath {
		m["path"] = s.Path(in)
	}
	return m
}

// FileLines is what an agent told of a memo reads after it: one line per file still kept, `[file N: /path (type, size)]`. "" for none.
func (s *Store) FileLines(infos []Info) string {
	var lines []string
	for _, in := range infos {
		if _, err := s.Lookup(in.ID); err == nil {
			lines = append(lines, FileLine(len(lines)+1, s.Path(in), in.Mime, in.Bytes))
		}
	}
	return strings.Join(lines, "\n")
}

// FileLine is how a memo's file n is shown: `[file N: /path (type, size)]`; where is its name for a reader not given the path.
func FileLine(n int, where, mime string, bytes int64) string {
	return fmt.Sprintf("[file %d: %s (%s, %s)]", n, where, mime, size(bytes))
}
