package attachments

import (
	"bytes"
	"database/sql"
	"encoding/base64"
	"fmt"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

func pngOf(w, h int) []byte {
	img := image.NewNRGBA(image.Rect(0, 0, w, h))
	for x := 0; x < w; x++ {
		for y := 0; y < h; y++ {
			img.Set(x, y, color.NRGBA{uint8(x), uint8(y), 7, 255})
		}
	}
	var b bytes.Buffer
	png.Encode(&b, img)
	return b.Bytes()
}

func testStore(t *testing.T) (*Store, *time.Time, *sql.DB) {
	now := time.Unix(1_800_000_000, 0)
	q, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { q.Close() })
	return &Store{Dir: t.TempDir() + "/attachments", Now: func() time.Time { return now }}, &now, q
}

func wantErr(t *testing.T, err error, code, field, gate string) {
	t.Helper()
	e, ok := err.(*rpc.Error)
	if !ok || e.Code != code || (field != "" && e.Data["field"] != field) || (gate != "" && e.Data["gate"] != gate) {
		t.Fatalf("got %v, want %s field=%q gate=%q", err, code, field, gate)
	}
}

func putAll(t *testing.T, s *Store, q db.Q, name, mime string, b []byte, chunk int) *Info {
	t.Helper()
	var up string
	for off := 0; ; off += chunk {
		end := min(off+chunk, len(b))
		out, err := s.Put(q, PutIn{Name: name, Mime: mime, Bytes: int64(len(b)), Offset: int64(off), Upload: up, Data: b[off:end]})
		if err != nil {
			t.Fatal(err)
		}
		up = out.Upload
		if out.Attachment != nil {
			return out.Attachment
		}
	}
}

func TestPutInChunksAndGetBack(t *testing.T) {
	s, _, q := testStore(t)
	b := pngOf(40, 30)
	info := putAll(t, s, q, "dir/../shot.png", "image/png", b, 100)
	if info.Name != "shot.png" || info.Bytes != int64(len(b)) || !ValidID(info.ID) {
		t.Fatalf("attachment %+v", info)
	}
	got, err := s.Get(info.ID, false, 0)
	if err != nil || !got.Done || got.Data != base64.StdEncoding.EncodeToString(b) {
		t.Fatalf("get %+v %v", got, err)
	}
	st, _ := os.Stat(s.Path(*info))
	if st.Mode().Perm() != 0o600 {
		t.Fatalf("file mode %v", st.Mode())
	}
	if !strings.HasSuffix(s.Path(*info), info.ID+".png") {
		t.Fatalf("path %s", s.Path(*info))
	}
}

func TestChunkedGetResumes(t *testing.T) {
	s, _, q := testStore(t)
	b := bytes.Repeat([]byte{1}, MaxChunk+10)
	copy(b, "\x89PNG\r\n\x1a\n")
	info := putAll(t, s, q, "a.png", "image/png", b, MaxChunk)
	first, err := s.Get(info.ID, false, 0)
	if err != nil || first.Done {
		t.Fatalf("first %+v %v", first, err)
	}
	rest, err := s.Get(info.ID, false, MaxChunk)
	if err != nil || !rest.Done || rest.Start != MaxChunk {
		t.Fatalf("rest %+v %v", rest, err)
	}
}

func TestWrongOffsetSaysWhatTheDaemonHolds(t *testing.T) {
	s, _, q := testStore(t)
	b := pngOf(4, 4)
	out, err := s.Put(q, PutIn{Name: "a.png", Mime: "image/png", Bytes: int64(len(b)), Data: b[:8]})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Put(q, PutIn{Name: "a.png", Mime: "image/png", Bytes: int64(len(b)), Offset: 3, Upload: out.Upload, Data: b[8:20]})
	wantErr(t, err, "bad_request", "offset", "")
	if err.(*rpc.Error).Data["received"] != int64(8) {
		t.Fatalf("data %v", err.(*rpc.Error).Data)
	}
}

func TestTypeIsCheckedByTheFirstBytes(t *testing.T) {
	s, _, q := testStore(t)
	_, err := s.Put(q, PutIn{Name: "a.png", Mime: "not a type", Bytes: 5, Data: []byte("<svg>")})
	wantErr(t, err, "bad_request", "mime", "")
	_, err = s.Put(q, PutIn{Name: "a.png", Mime: "image/png", Bytes: 5, Data: []byte("hello")})
	wantErr(t, err, "bad_request", "data", "")
	if m, _ := os.ReadDir(s.sub("uploads")); len(m) != 0 {
		t.Fatalf("a refused upload is dropped: %v", m)
	}
	// a PNG that says it is a JPEG
	b := pngOf(2, 2)
	_, err = s.Put(q, PutIn{Name: "a.jpg", Mime: "image/jpeg", Bytes: int64(len(b)), Data: b})
	wantErr(t, err, "bad_request", "data", "")
	for name, b := range map[string]string{"image/gif": "GIF89a....", "image/webp": "RIFF\x00\x00\x00\x00WEBPVP8 ", "image/jpeg": "\xff\xd8\xff\xe0.."} {
		if _, err := s.Put(q, PutIn{Name: "x", Mime: name, Bytes: int64(len(b)), Data: []byte(b)}); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
	}
}

func TestLimits(t *testing.T) {
	s, _, q := testStore(t)
	_, err := s.Put(q, PutIn{Name: "a.png", Mime: "image/png", Bytes: MaxImage + 1, Data: pngOf(1, 1)})
	wantErr(t, err, "refused", "", "limit")
	for i := 0; i < MaxOpen; i++ {
		if _, err := s.Put(q, PutIn{Name: "a.png", Mime: "image/png", Bytes: 1000, Data: []byte("\x89PNG")}); err != nil {
			t.Fatal(err)
		}
	}
	_, err = s.Put(q, PutIn{Name: "a.png", Mime: "image/png", Bytes: 1000, Data: []byte("\x89PNG")})
	wantErr(t, err, "refused", "", "limit")
	_, err = s.Put(q, PutIn{Name: "a.png", Mime: "image/png", Bytes: 1000, Offset: 0, Upload: "0000000000000000", Data: []byte("\x89PNG")})
	wantErr(t, err, "not_found", "", "")
}

func TestUnsentTotalIsLimited(t *testing.T) {
	s, _, q := testStore(t)
	for i := 0; i < 10; i++ { // 10 uploads of 10 MiB open: 100 MiB
		if _, err := s.Put(q, PutIn{Name: "a.png", Mime: "image/png", Bytes: MaxImage, Data: []byte("\x89PNG")}); err != nil {
			t.Fatal(err)
		}
	}
	_, err := s.Put(q, PutIn{Name: "a.png", Mime: "image/png", Bytes: 1, Data: []byte("\x89")})
	wantErr(t, err, "refused", "", "limit")
}

func TestGetShapes(t *testing.T) {
	s, _, _ := testStore(t)
	_, err := s.Get("0000000000000000", false, 0)
	wantErr(t, err, "not_found", "", "")
	_, err = s.Lookup("../etc/passwd")
	wantErr(t, err, "bad_request", "id", "")
}

func TestThumbIsSmall(t *testing.T) {
	s, _, q := testStore(t)
	b := pngOf(1000, 500)
	info := putAll(t, s, q, "big.png", "image/png", b, MaxChunk)
	g, err := s.Get(info.ID, true, 0)
	if err != nil || !g.Done || g.Mime != "image/png" {
		t.Fatalf("thumb %+v %v", g, err)
	}
	raw, _ := base64.StdEncoding.DecodeString(g.Data)
	img, err := png.Decode(bytes.NewReader(raw))
	if err != nil || img.Bounds().Dx() != ThumbSide || img.Bounds().Dy() != 160 {
		t.Fatalf("thumb %v %v", img.Bounds(), err)
	}
	if g.Bytes >= int64(len(b)) {
		t.Fatalf("thumb %d bytes, original %d", g.Bytes, len(b))
	}
	// a webp has no decoder here: the image itself
	w := []byte("RIFF\x00\x00\x00\x00WEBPVP8 ")
	wi := putAll(t, s, q, "a.webp", "image/webp", w, 100)
	g, err = s.Get(wi.ID, true, 0)
	if err != nil || g.Mime != "image/webp" || g.Bytes != int64(len(w)) {
		t.Fatalf("webp thumb %+v %v", g, err)
	}
}

func TestSweep(t *testing.T) {
	s, now, q := testStore(t)
	b := pngOf(2, 2)
	open, _ := s.Put(q, PutIn{Name: "a.png", Mime: "image/png", Bytes: 1000, Data: []byte("\x89PNG")})
	unsent := putAll(t, s, q, "u.png", "image/png", b, 1000)
	sent := putAll(t, s, q, "s.png", "image/png", b, 1000)
	if _, err := s.Claim([]string{sent.ID}); err != nil {
		t.Fatal(err)
	}
	*now = now.Add(UploadTTL + time.Minute)
	if err := s.Sweep(q); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(s.sub("uploads", open.Upload+".part")); err == nil {
		t.Fatal("an upload not finished in an hour stays")
	}
	if _, err := s.Lookup(unsent.ID); err != nil {
		t.Fatal(err)
	}
	*now = now.Add(UnsentTTL)
	if err := s.Sweep(q); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Lookup(unsent.ID); err == nil {
		t.Fatal("an image no message names stays past a day")
	}
	if _, err := s.Lookup(sent.ID); err != nil {
		t.Fatal("an image a message names goes within a day")
	}
	*now = now.Add(KeepFor)
	if err := s.Sweep(q); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Lookup(sent.ID); err == nil {
		t.Fatal("an image stays past its 30 days")
	}
}

func TestAnImageATaskNamesIsKeptWhileTheTaskIsOpenAndThirtyDaysAfter(t *testing.T) {
	s, now, q := testStore(t)
	inBrief := putAll(t, s, q, "b.png", "image/png", pngOf(2, 2), 1000)
	inMemo := putAll(t, s, q, "m.png", "image/png", pngOf(3, 3), 1000)
	inNote := putAll(t, s, q, "n.png", "image/png", pngOf(4, 4), 1000)
	for _, stmt := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'w', 'boss', 1)",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body) VALUES(10, 'boss', 'w', 'task', 'landed', 1, 'see ![x](attachment:" + inBrief.ID + ")')",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10)",
		"INSERT INTO task_memos(task_id, author, body, created_at) VALUES(1, 'w', 'and ![y](attachment:" + inMemo.ID + ")', 1)",
		`INSERT INTO events(ts, agent, kind, payload) VALUES(1, 'boss', 'reject_task', '{"task_id": 1, "note": "no\n![z](attachment:` + inNote.ID + `)"}')`,
	} {
		if _, err := q.Exec(stmt); err != nil {
			t.Fatalf("%s: %v", stmt, err)
		}
	}
	all := []*Info{inBrief, inMemo, inNote}
	sweep := func(after time.Duration, kept bool) {
		t.Helper()
		*now = now.Add(after)
		if err := s.Sweep(q); err != nil {
			t.Fatal(err)
		}
		for _, i := range all {
			if _, err := s.Lookup(i.ID); (err == nil) != kept {
				t.Fatalf("%s kept %v, want %v", i.Name, err == nil, kept)
			}
		}
	}
	sweep(2*KeepFor, true) // never claimed, never swept while the task is open
	if _, err := q.Exec("UPDATE tasks SET done_evidence = 'ok', done_at = ? WHERE id = 1", now.Unix()); err != nil {
		t.Fatal(err)
	}
	sweep(KeepFor-time.Hour, true)
	sweep(2*time.Hour, false)
}

func TestATaskHoldsOnlyWhatItsBriefMemosAndNotesName(t *testing.T) {
	s, _, q := testStore(t)
	said := putAll(t, s, q, "s.png", "image/png", pngOf(2, 2), 1000)
	for _, stmt := range []string{
		"INSERT INTO tasks(id, title, owner, opened_by, created_at) VALUES(1, 't', 'w', 'boss', 1)",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body) VALUES(10, 'boss', 'w', 'task', 'landed', 1, 'the brief')",
		"INSERT INTO requests(id, requester, target, kind, state, ts, body) VALUES(11, 'w', 'boss', 'say', 'landed', 2, 'a say ![x](attachment:" + said.ID + ")')",
		"INSERT INTO task_requests(task_id, request_id) VALUES(1, 10), (1, 11)",
	} {
		if _, err := q.Exec(stmt); err != nil {
			t.Fatalf("%s: %v", stmt, err)
		}
	}
	held, err := Held(q, 5)
	if err != nil || len(held) != 0 {
		t.Fatalf("a say linked to a task is not its brief: %v %v", held, err)
	}
}

func TestPartsCutATextAtItsImagesAndSayWhichAreGone(t *testing.T) {
	s, _, q := testStore(t)
	kept := putAll(t, s, q, "k.png", "image/png", pngOf(2, 2), 1000)
	gone := putAll(t, s, q, "g.png", "image/png", pngOf(3, 3), 1000)
	s.remove(*gone)
	text := "see ![one](attachment:" + kept.ID + ")![two](attachment:" + gone.ID + ") and wrap"
	if s.Parts("plain", true) != nil {
		t.Fatal("a text naming no image has no parts")
	}
	parts := s.Parts(text, false)
	if len(parts) != 4 {
		t.Fatalf("%v", parts)
	}
	words := ""
	for _, p := range parts {
		if w, ok := p.(map[string]any)["text"].(string); ok {
			words += w
		}
	}
	if words != "see  and wrap" {
		t.Fatalf("the text parts joined are the text without its references: %q", words)
	}
	one, two := parts[1].(map[string]any), parts[2].(map[string]any)
	if one["caption"] != "one" || one["image"].(map[string]any)["mime"] != "image/png" || one["path"] != nil || one["gone"] != nil {
		t.Fatalf("a kept image, no path for a person: %v", one)
	}
	if two["gone"] != true || len(two["image"].(map[string]any)) != 1 || two["caption"] != "two" {
		t.Fatalf("a gone image keeps its caption and id: %v", two)
	}
	if p := s.Parts(text, true)[1].(map[string]any)["path"]; p != s.Path(*kept) {
		t.Fatalf("an agent gets the path: %v", p)
	}
	if lines := s.MessageLines(q, 0, text, text); !strings.HasPrefix(lines, "[image 1: "+s.Path(*kept)+" (image/png, ") || strings.Contains(lines, "\n") {
		t.Fatalf("one line per kept image: %q", lines)
	}
}

func TestKeepRefsPutsBackWhatACutLost(t *testing.T) {
	whole := "words ![a](attachment:0000000000000001) more ![b](attachment:0000000000000002)"
	if got := KeepRefs(whole[:12], whole); got != "words  ![a](attachment:0000000000000001) ![b](attachment:0000000000000002)" {
		t.Fatalf("%q", got)
	}
	if got := KeepRefs(whole[:3], whole); got != "wor ![a](attachment:0000000000000001) ![b](attachment:0000000000000002)" {
		t.Fatalf("every lost reference comes back: %q", got)
	}
	if KeepRefs(whole, whole) != whole {
		t.Fatal("a cut that lost nothing is unchanged")
	}
}

func TestAMessagesImagesAreItsReferencesThenWhatItWasSentWith(t *testing.T) {
	s, _, q := testStore(t)
	var ids []string
	for i := range 7 {
		ids = append(ids, putAll(t, s, q, "i.png", "image/png", pngOf(2+i, 2), 1000).ID)
	}
	ref := func(id string) string { return "![c](attachment:" + id + ")" }
	body := "look " + ref(ids[0]) + " and " + ref(ids[1])
	_, err := s.CheckMessage(q, body+ref(ids[2])+ref(ids[3]), "body", ids[3:7], false)
	wantErr(t, err, "bad_request", "attachments", "")
	listed, err := s.CheckMessage(q, body, "body", []string{ids[2], ids[1]}, false)
	if err != nil || len(listed) != 1 || listed[0].ID != ids[2] {
		t.Fatalf("an id in both is counted once and recorded only as listed: %v %v", listed, err)
	}
	if err := Record(q, 7, listed); err != nil {
		t.Fatal(err)
	}
	var order []string
	for _, m := range s.MessageMaps(q, 7, body) {
		order = append(order, m.(map[string]any)["id"].(string))
	}
	if fmt.Sprint(order) != fmt.Sprint(ids[:3]) {
		t.Fatalf("references first, then the listed: %v", order)
	}
	parts := s.MessageParts(q, 7, body, false)
	if last := parts[len(parts)-1].(map[string]any); len(parts) != 5 || last["caption"] != "" || last["image"].(map[string]any)["id"] != ids[2] {
		t.Fatalf("a listed image is a part after the text: %v", parts)
	}
	lines := strings.Split(s.MessageLines(q, 7, body, ref(ids[4])), "\n")
	if len(lines) != 4 || !strings.Contains(lines[2], ids[2]) || !strings.HasPrefix(lines[3], "[image 4: ") {
		t.Fatalf("lines number the message's images, then the memos': %v", lines)
	}
	if _, err := s.CheckMessage(q, ref(ids[5]), "body", nil, true); err == nil {
		t.Fatal("an agent may not name an image no task or message shows")
	}
	if _, err := s.CheckMessage(q, ref(ids[2]), "body", nil, true); err != nil {
		t.Fatalf("an agent may pass on an image a message was sent with: %v", err)
	}
	q.Exec("INSERT INTO requests(id, requester, target, kind, state, ts, body) VALUES(8, 'human', 'w', 'say', 'landed', 1, ?)", ref(ids[6]))
	if _, err := s.CheckMessage(q, ref(ids[6]), "text", nil, true); err != nil {
		t.Fatalf("or one a message names: %v", err)
	}
}

func TestAPutImageKnowsItsSize(t *testing.T) {
	s, _, q := testStore(t)
	info := putAll(t, s, q, "shot.png", "image/png", pngOf(40, 30), 100)
	if info.Width != 40 || info.Height != 30 {
		t.Fatalf("size %dx%d", info.Width, info.Height)
	}
	if got, _ := s.Lookup(info.ID); got.Width != 40 || got.Height != 30 {
		t.Fatalf("kept size %dx%d", got.Width, got.Height)
	}
	if m := info.Map(); m["width"] != 40 || m["height"] != 30 {
		t.Fatalf("map %v", m)
	}
	odd := putAll(t, s, q, "x.gif", "image/gif", []byte("GIF89a...."), 100) // a header too short to read
	if _, ok := odd.Map()["width"]; ok || odd.Width != 0 {
		t.Fatalf("an unreadable size was given: %+v", odd)
	}
}

func TestWebPSizeFromEachHeaderKind(t *testing.T) {
	head := func(kind string, body ...byte) []byte {
		b := append([]byte("RIFF\x00\x00\x00\x00WEBP"+kind+"\x00\x00\x00\x00"), body...)
		return append(b, make([]byte, 30-len(b))...)
	}
	bits := uint32(5-1) | uint32(7-1)<<14
	for name, c := range map[string]struct {
		b    []byte
		w, h int
	}{
		"lossy":                   {head("VP8 ", 0, 0, 0, 0x9d, 0x01, 0x2a, 5, 0, 7, 0), 5, 7},
		"lossless":                {head("VP8L", 0x2f, byte(bits), byte(bits>>8), byte(bits>>16), byte(bits>>24)), 5, 7},
		"extended":                {head("VP8X", 0, 0, 0, 0, 4, 0, 0, 6, 0, 0), 5, 7},
		"unknown":                 {head("ABCD"), 0, 0},
		"lossy, wrong start code": {head("VP8 ", 0, 0, 0, 0x9d, 0x01, 0x2b, 5, 0, 7, 0), 0, 0},
		"not riff":                {append([]byte("JUNK"), head("VP8X", 0, 0, 0, 0, 4, 0, 0, 6, 0, 0)[4:]...), 0, 0},
		"short":                   {[]byte("RIFF\x00\x00\x00\x00WEBPVP8X"), 0, 0},
	} {
		if w, h := webpSize(c.b); w != c.w || h != c.h {
			t.Errorf("%s: %dx%d, want %dx%d", name, w, h, c.w, c.h)
		}
	}
}

// jpegWithOrientation is a w x h JPEG carrying an Exif segment that says orientation o, as a phone writes one; pad fills the segment
// out, as a camera's thumbnail and maker notes do.
func jpegWithOrientation(t *testing.T, w, h int, o uint16, pad int) []byte {
	var b bytes.Buffer
	if err := jpeg.Encode(&b, image.NewRGBA(image.Rect(0, 0, w, h)), nil); err != nil {
		t.Fatal(err)
	}
	tiff := []byte("MM\x00\x2a\x00\x00\x00\x08\x00\x01\x01\x12\x00\x03\x00\x00\x00\x01")
	tiff = append(tiff, byte(o>>8), byte(o), 0, 0, 0, 0, 0, 0)
	seg := append(append([]byte("Exif\x00\x00"), tiff...), make([]byte, pad)...)
	app1 := append([]byte{0xff, 0xe1, byte((len(seg) + 2) >> 8), byte(len(seg) + 2)}, seg...)
	return append(append([]byte{0xff, 0xd8}, app1...), b.Bytes()[2:]...)
}

func TestAJpegTurnedByItsExifIsSizedAsItIsDrawn(t *testing.T) {
	s, _, q := testStore(t)
	full := 65535 - 2 - 6 - 26 // the largest APP1: its length field at 65535, longer than the head that is read
	for _, c := range []struct {
		o    uint16
		pad  int
		want [2]int
	}{{1, 0, [2]int{40, 30}}, {6, 0, [2]int{30, 40}}, {8, 0, [2]int{30, 40}}, {9, 0, [2]int{40, 30}}, {6, full, [2]int{30, 40}}} {
		info := putAll(t, s, q, "photo.jpg", "image/jpeg", jpegWithOrientation(t, 40, 30, c.o, c.pad), 100000)
		if info.Width != c.want[0] || info.Height != c.want[1] {
			t.Errorf("orientation %d, pad %d: %dx%d, want %dx%d", c.o, c.pad, info.Width, info.Height, c.want[0], c.want[1])
		}
	}
}

// Any file is kept as sent, under the type the client gave, stored with an ending from its name, and told to the agent by the same line.
func TestAnyFileIsKeptAsSentAndToldByTheSamePathLine(t *testing.T) {
	s, _, q := testStore(t)
	pdf := []byte("%PDF-1.4 not an image")
	for _, c := range []struct{ name, mime, ending string }{
		{"spec.PDF", "application/pdf", ".pdf"}, {"notes", "text/plain", ".bin"}, {"run.sh;x", "application/x-sh", ".bin"},
		{"a.verylongending", "application/zip", ".bin"}, {"fake.png", "image/svg+xml", ".png"},
	} {
		in := putAll(t, s, q, c.name, c.mime, pdf, 7)
		if in.Mime != c.mime || in.Width != 0 || !strings.HasSuffix(s.Path(*in), c.ending) {
			t.Fatalf("%+v at %s", in, s.Path(*in))
		}
		got, err := s.Get(in.ID, true, 0)
		if err != nil || got.Mime != c.mime || got.Bytes != int64(len(pdf)) {
			t.Fatalf("a thumb of a file is the file: %+v %v", got, err)
		}
		if st, err := os.Stat(s.Path(*in)); err != nil || st.Mode().Perm() != 0o600 {
			t.Fatalf("a file is never executable: %v %v", st, err)
		}
	}
	in := putAll(t, s, q, "spec.pdf", "application/pdf", pdf, 100)
	line := s.line(1, *in)
	if want := "[image 1: " + s.Path(*in) + " (application/pdf, 21 B)]"; line != want {
		t.Fatalf("%q, want %q", line, want)
	}
	// an image that says so is still checked
	_, err := s.Put(q, PutIn{Name: "a.png", Mime: "image/png", Bytes: 5, Data: []byte("hello")})
	wantErr(t, err, "bad_request", "data", "")
}
