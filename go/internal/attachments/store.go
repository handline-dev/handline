// Package attachments keeps the images a person attaches to a chat message (contract x-attachments): uploaded in chunks, stored under the
// daemon's home, and named to the agent by path. All state is in files, so a restart loses nothing and the database has no new table.
package attachments

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

const (
	MaxImage      = 10 << 20  // bytes of one image
	MaxPerMessage = 6         // images on one message
	MaxChunk      = 512 << 10 // bytes of file in one chunk
	MaxOpen       = 20        // uploads open at once
	MaxUnsent     = 100 << 20 // bytes of uploads open and images no message names
	MaxName       = 128       // characters of a file name kept
	UploadTTL     = time.Hour
	UnsentTTL     = 24 * time.Hour
	KeepFor       = 30 * 24 * time.Hour // after the message that names the image
)

// kinds are the types taken, by the mime a client says and the extension the file is stored with.
var kinds = map[string]string{"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}

var idPattern = regexp.MustCompile(`^[0-9a-f]{16}$`)

// Info is the Attachment of the contract.
type Info struct {
	ID    string `json:"id"`
	Name  string `json:"name"`
	Mime  string `json:"mime"`
	Bytes int64  `json:"bytes"`
}

// Map is the Info as a member of a reply.
func (i Info) Map() map[string]any {
	return map[string]any{"id": i.ID, "name": i.Name, "mime": i.Mime, "bytes": i.Bytes}
}

// Store is the attachments folder.
type Store struct {
	Dir string
	Now func() time.Time
}

// New is the store under home.
func New(home string) *Store { return &Store{Dir: filepath.Join(home, "attachments"), Now: time.Now} }

func (s *Store) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}

func (s *Store) sub(parts ...string) string {
	return filepath.Join(append([]string{s.Dir}, parts...)...)
}

// stored is the sidecar of a finished image.
type stored struct {
	Info
	Created int64 `json:"created"`
	Claimed int64 `json:"claimed,omitempty"` // when a message last named it
}

// upload is the sidecar of an unfinished one; what has arrived is the size of its part file.
type upload struct {
	Name    string `json:"name"`
	Mime    string `json:"mime"`
	Bytes   int64  `json:"bytes"`
	Created int64  `json:"created"`
}

func (s *Store) ensure() error {
	for _, d := range []string{s.Dir, s.sub("uploads"), s.sub("meta"), s.sub("thumbs")} {
		if err := os.MkdirAll(d, 0o700); err != nil {
			return err
		}
	}
	return nil
}

func newID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b[:])
}

func badParam(message, field string) *rpc.Error {
	return rpc.BadRequestError(message, field, rpc.InvalidParams)
}

// ValidID is whether id has the shape of an AttachmentId.
func ValidID(id string) bool { return idPattern.MatchString(id) }

// Sniff is the mime the first bytes say, or "".
func Sniff(b []byte) string {
	switch {
	case bytes.HasPrefix(b, []byte("\x89PNG\r\n\x1a\n")):
		return "image/png"
	case bytes.HasPrefix(b, []byte("\xff\xd8\xff")):
		return "image/jpeg"
	case bytes.HasPrefix(b, []byte("GIF87a")), bytes.HasPrefix(b, []byte("GIF89a")):
		return "image/gif"
	case len(b) >= 12 && bytes.HasPrefix(b, []byte("RIFF")) && bytes.Equal(b[8:12], []byte("WEBP")):
		return "image/webp"
	}
	return ""
}

// plausible is whether a file that has begun with b can still turn out to be mime: a refusal need not wait for the whole upload.
func plausible(b []byte, mime string) bool {
	sig := map[string][]string{"image/png": {"\x89PNG\r\n\x1a\n"}, "image/jpeg": {"\xff\xd8\xff"}, "image/gif": {"GIF87a", "GIF89a"}}[mime]
	if mime == "image/webp" {
		for i, want := range map[int]string{0: "RIFF", 8: "WEBP"} {
			if i < len(b) && !bytes.HasPrefix(b[i:], []byte(want[:min(len(want), len(b)-i)])) {
				return false
			}
		}
		return true
	}
	for _, p := range sig {
		n := min(len(p), len(b))
		if string(b[:n]) == p[:n] {
			return true
		}
	}
	return false
}

// FileName is name as shown: no folder, no unprintable character, at most MaxName characters.
func FileName(name string) string {
	if i := strings.LastIndexAny(name, `/\`); i >= 0 {
		name = name[i+1:]
	}
	name = strings.TrimSpace(textsafe.Oneline(name))
	if utf8.RuneCountInString(name) > MaxName {
		name = string([]rune(name)[:MaxName])
	}
	return name
}

func writeJSON(path string, v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func readJSON(path string, v any) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, v)
}

func unknownUpload(id string) *rpc.Error {
	e := rpc.NotFoundError("no such upload (it was finished or has expired): " + id)
	e.Data = map[string]any{"what": "upload", "name": id}
	return e
}

func unknownImage(id string) *rpc.Error {
	e := rpc.NotFoundError("no such attachment (it was never put or has been removed): " + id)
	e.Data = map[string]any{"what": "attachment", "name": id}
	return e
}

func limit(reason string) *rpc.Error { return rpc.Refused(reason, "limit", "") }

// PutIn is attachment.put's parameters once their shapes are checked.
type PutIn struct {
	Name, Mime string
	Bytes      int64
	Offset     int64
	Upload     string
	Data       []byte
}

// PutOut is the reply: the upload to name next, how much the daemon holds, and the Attachment once the file is whole.
type PutOut struct {
	Upload     string
	Received   int64
	Attachment *Info
}

// Put takes one chunk.
func (s *Store) Put(in PutIn) (*PutOut, error) {
	if err := s.ensure(); err != nil {
		return nil, err
	}
	s.Sweep()
	if _, ok := kinds[in.Mime]; !ok {
		return nil, badParam("mime is image/png, image/jpeg, image/gif or image/webp", "mime")
	}
	if in.Bytes <= 0 {
		return nil, badParam("bytes is the size of the whole file", "bytes")
	}
	if len(in.Data) > MaxChunk {
		return nil, badParam(fmt.Sprintf("a chunk is at most %d KiB of file", MaxChunk>>10), "data")
	}
	id := in.Upload
	if id == "" {
		if in.Offset != 0 {
			return nil, badParam("a first chunk starts at offset 0", "offset")
		}
		var err error
		if id, err = s.open(in); err != nil {
			return nil, err
		}
	} else if !ValidID(id) {
		return nil, badParam("upload is an upload id", "upload")
	}
	var up upload
	if err := readJSON(s.sub("uploads", id+".json"), &up); err != nil {
		return nil, unknownUpload(id)
	}
	part := s.sub("uploads", id+".part")
	st, err := os.Stat(part)
	if err != nil {
		return nil, unknownUpload(id)
	}
	have := st.Size()
	if in.Bytes != up.Bytes {
		return nil, badParam("bytes is not what the upload began with", "bytes")
	}
	if in.Offset != have {
		e := badParam(fmt.Sprintf("offset %d is not where the upload stands: the daemon holds %d bytes", in.Offset, have), "offset")
		e.Data["received"] = have
		return nil, e
	}
	if have+int64(len(in.Data)) > up.Bytes {
		s.drop(id)
		return nil, badParam("the chunk runs past the end of the file", "data")
	}
	f, err := os.OpenFile(part, os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return nil, err
	}
	_, err = f.Write(in.Data)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return nil, err
	}
	have += int64(len(in.Data))
	head, err := readHead(part, 16)
	if err != nil {
		return nil, err
	}
	if !plausible(head, up.Mime) {
		s.drop(id)
		return nil, badParam("the file is not "+up.Mime+" by its first bytes", "data")
	}
	out := &PutOut{Upload: id, Received: have}
	if have < up.Bytes {
		return out, nil
	}
	if Sniff(head) != up.Mime {
		s.drop(id)
		return nil, badParam("the file is not "+up.Mime+" by its first bytes", "data")
	}
	info, err := s.finish(id, up)
	if err != nil {
		return nil, err
	}
	out.Attachment = info
	return out, nil
}

func readHead(path string, n int) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	b := make([]byte, n)
	m, err := f.Read(b)
	if err != nil && m == 0 && n > 0 {
		if errors.Is(err, os.ErrNotExist) {
			return nil, err
		}
		return b[:0], nil
	}
	return b[:m], nil
}

// open starts an upload, after the limits.
func (s *Store) open(in PutIn) (string, error) {
	if in.Bytes > MaxImage {
		return "", limit(fmt.Sprintf("an image is at most %d MiB", MaxImage>>20))
	}
	open, unsent := 0, int64(0)
	ups, _ := filepath.Glob(s.sub("uploads", "*.json"))
	for _, p := range ups {
		var u upload
		if readJSON(p, &u) == nil {
			open++
			unsent += u.Bytes
		}
	}
	if open >= MaxOpen {
		return "", limit(fmt.Sprintf("%d uploads are open already: send or wait for them", MaxOpen))
	}
	metas, _ := filepath.Glob(s.sub("meta", "*.json"))
	for _, p := range metas {
		var im stored
		if readJSON(p, &im) == nil && im.Claimed == 0 {
			unsent += im.Bytes
		}
	}
	if unsent+in.Bytes > MaxUnsent {
		return "", limit(fmt.Sprintf("more than %d MiB of images wait unsent", MaxUnsent>>20))
	}
	id := newID()
	if err := os.WriteFile(s.sub("uploads", id+".part"), nil, 0o600); err != nil {
		return "", err
	}
	up := upload{Name: FileName(in.Name), Mime: in.Mime, Bytes: in.Bytes, Created: s.now().Unix()}
	if err := writeJSON(s.sub("uploads", id+".json"), up); err != nil {
		os.Remove(s.sub("uploads", id+".part"))
		return "", err
	}
	return id, nil
}

func (s *Store) drop(id string) {
	os.Remove(s.sub("uploads", id+".json"))
	os.Remove(s.sub("uploads", id+".part"))
}

func (s *Store) finish(uploadID string, up upload) (*Info, error) {
	id := newID()
	im := stored{Info: Info{ID: id, Name: up.Name, Mime: up.Mime, Bytes: up.Bytes}, Created: s.now().Unix()}
	if err := os.Rename(s.sub("uploads", uploadID+".part"), s.Path(im.Info)); err != nil {
		return nil, err
	}
	if err := writeJSON(s.sub("meta", id+".json"), im); err != nil {
		os.Remove(s.Path(im.Info))
		return nil, err
	}
	os.Remove(s.sub("uploads", uploadID+".json"))
	return &im.Info, nil
}

// Path is where the image is stored: named by its id and the extension of its type, never by the name a client gave.
func (s *Store) Path(i Info) string { return s.sub(i.ID + "." + kinds[i.Mime]) }

// Lookup is the Attachment of id; not_found when it was never put or has been removed.
func (s *Store) Lookup(id string) (Info, error) {
	var im stored
	if !ValidID(id) {
		return im.Info, badParam("id is an attachment id", "id")
	}
	if err := readJSON(s.sub("meta", id+".json"), &im); err != nil {
		return im.Info, unknownImage(id)
	}
	if _, err := os.Stat(s.Path(im.Info)); err != nil {
		return im.Info, unknownImage(id)
	}
	return im.Info, nil
}

// Claim records that a message names the images, which keeps them KeepFor from now, and answers their Attachments in order.
func (s *Store) Claim(ids []string) ([]Info, error) {
	out := make([]Info, 0, len(ids))
	for _, id := range ids {
		info, err := s.Lookup(id)
		if err != nil {
			return nil, err
		}
		out = append(out, info)
	}
	for _, info := range out {
		var im stored
		if readJSON(s.sub("meta", info.ID+".json"), &im) == nil {
			im.Claimed = s.now().Unix()
			if err := writeJSON(s.sub("meta", info.ID+".json"), im); err != nil {
				return nil, err
			}
		}
	}
	return out, nil
}

// Sweep removes what has outlived its time: an upload not finished within UploadTTL, an image no message names after UnsentTTL, and one a
// message names KeepFor after that message.
func (s *Store) Sweep() {
	now := s.now()
	for _, p := range s.glob("uploads", "*.json") {
		var u upload
		if readJSON(p, &u) != nil || now.Sub(time.Unix(u.Created, 0)) > UploadTTL {
			s.drop(strings.TrimSuffix(filepath.Base(p), ".json"))
		}
	}
	for _, p := range s.glob("meta", "*.json") {
		var im stored
		if err := readJSON(p, &im); err != nil {
			continue
		}
		expired := now.Sub(time.Unix(im.Created, 0)) > UnsentTTL
		if im.Claimed != 0 {
			expired = now.Sub(time.Unix(im.Claimed, 0)) > KeepFor
		}
		if expired {
			s.remove(im.Info)
		}
	}
	for _, p := range s.glob("uploads", "*.part") { // a part whose sidecar is gone
		if _, err := os.Stat(strings.TrimSuffix(p, ".part") + ".json"); err != nil {
			os.Remove(p)
		}
	}
}

func (s *Store) glob(dir, pattern string) []string {
	m, _ := filepath.Glob(s.sub(dir, pattern))
	return m
}

func (s *Store) remove(i Info) {
	os.Remove(s.Path(i))
	os.Remove(s.sub("thumbs", i.ID+".thumb"))
	os.Remove(s.sub("meta", i.ID+".json"))
}
