package attachments

import (
	"bytes"
	"encoding/base64"
	"image"
	"image/color"
	"image/png"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/rpc"
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

func testStore(t *testing.T) (*Store, *time.Time) {
	now := time.Unix(1_800_000_000, 0)
	return &Store{Dir: t.TempDir() + "/attachments", Now: func() time.Time { return now }}, &now
}

func wantErr(t *testing.T, err error, code, field, gate string) {
	t.Helper()
	e, ok := err.(*rpc.Error)
	if !ok || e.Code != code || (field != "" && e.Data["field"] != field) || (gate != "" && e.Data["gate"] != gate) {
		t.Fatalf("got %v, want %s field=%q gate=%q", err, code, field, gate)
	}
}

func putAll(t *testing.T, s *Store, name, mime string, b []byte, chunk int) *Info {
	t.Helper()
	var up string
	for off := 0; ; off += chunk {
		end := min(off+chunk, len(b))
		out, err := s.Put(PutIn{Name: name, Mime: mime, Bytes: int64(len(b)), Offset: int64(off), Upload: up, Data: b[off:end]})
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
	s, _ := testStore(t)
	b := pngOf(40, 30)
	info := putAll(t, s, "dir/../shot.png", "image/png", b, 100)
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
	s, _ := testStore(t)
	b := bytes.Repeat([]byte{1}, MaxChunk+10)
	copy(b, "\x89PNG\r\n\x1a\n")
	info := putAll(t, s, "a.png", "image/png", b, MaxChunk)
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
	s, _ := testStore(t)
	b := pngOf(4, 4)
	out, err := s.Put(PutIn{Name: "a.png", Mime: "image/png", Bytes: int64(len(b)), Data: b[:8]})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Put(PutIn{Name: "a.png", Mime: "image/png", Bytes: int64(len(b)), Offset: 3, Upload: out.Upload, Data: b[8:20]})
	wantErr(t, err, "bad_request", "offset", "")
	if err.(*rpc.Error).Data["received"] != int64(8) {
		t.Fatalf("data %v", err.(*rpc.Error).Data)
	}
}

func TestTypeIsCheckedByTheFirstBytes(t *testing.T) {
	s, _ := testStore(t)
	_, err := s.Put(PutIn{Name: "a.png", Mime: "image/svg+xml", Bytes: 5, Data: []byte("<svg>")})
	wantErr(t, err, "bad_request", "mime", "")
	_, err = s.Put(PutIn{Name: "a.png", Mime: "image/png", Bytes: 5, Data: []byte("hello")})
	wantErr(t, err, "bad_request", "data", "")
	if m, _ := os.ReadDir(s.sub("uploads")); len(m) != 0 {
		t.Fatalf("a refused upload is dropped: %v", m)
	}
	// a PNG that says it is a JPEG
	b := pngOf(2, 2)
	_, err = s.Put(PutIn{Name: "a.jpg", Mime: "image/jpeg", Bytes: int64(len(b)), Data: b})
	wantErr(t, err, "bad_request", "data", "")
	for name, b := range map[string]string{"image/gif": "GIF89a....", "image/webp": "RIFF\x00\x00\x00\x00WEBPVP8 ", "image/jpeg": "\xff\xd8\xff\xe0.."} {
		if _, err := s.Put(PutIn{Name: "x", Mime: name, Bytes: int64(len(b)), Data: []byte(b)}); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
	}
}

func TestLimits(t *testing.T) {
	s, _ := testStore(t)
	_, err := s.Put(PutIn{Name: "a.png", Mime: "image/png", Bytes: MaxImage + 1, Data: pngOf(1, 1)})
	wantErr(t, err, "refused", "", "limit")
	for i := 0; i < MaxOpen; i++ {
		if _, err := s.Put(PutIn{Name: "a.png", Mime: "image/png", Bytes: 1000, Data: []byte("\x89PNG")}); err != nil {
			t.Fatal(err)
		}
	}
	_, err = s.Put(PutIn{Name: "a.png", Mime: "image/png", Bytes: 1000, Data: []byte("\x89PNG")})
	wantErr(t, err, "refused", "", "limit")
	_, err = s.Put(PutIn{Name: "a.png", Mime: "image/png", Bytes: 1000, Offset: 0, Upload: "0000000000000000", Data: []byte("\x89PNG")})
	wantErr(t, err, "not_found", "", "")
}

func TestUnsentTotalIsLimited(t *testing.T) {
	s, _ := testStore(t)
	for i := 0; i < 10; i++ { // 10 uploads of 10 MiB open: 100 MiB
		if _, err := s.Put(PutIn{Name: "a.png", Mime: "image/png", Bytes: MaxImage, Data: []byte("\x89PNG")}); err != nil {
			t.Fatal(err)
		}
	}
	_, err := s.Put(PutIn{Name: "a.png", Mime: "image/png", Bytes: 1, Data: []byte("\x89")})
	wantErr(t, err, "refused", "", "limit")
}

func TestGetShapes(t *testing.T) {
	s, _ := testStore(t)
	_, err := s.Get("0000000000000000", false, 0)
	wantErr(t, err, "not_found", "", "")
	_, err = s.Lookup("../etc/passwd")
	wantErr(t, err, "bad_request", "id", "")
}

func TestThumbIsSmall(t *testing.T) {
	s, _ := testStore(t)
	b := pngOf(1000, 500)
	info := putAll(t, s, "big.png", "image/png", b, MaxChunk)
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
	wi := putAll(t, s, "a.webp", "image/webp", w, 100)
	g, err = s.Get(wi.ID, true, 0)
	if err != nil || g.Mime != "image/webp" || g.Bytes != int64(len(w)) {
		t.Fatalf("webp thumb %+v %v", g, err)
	}
}

func TestSweep(t *testing.T) {
	s, now := testStore(t)
	b := pngOf(2, 2)
	open, _ := s.Put(PutIn{Name: "a.png", Mime: "image/png", Bytes: 1000, Data: []byte("\x89PNG")})
	unsent := putAll(t, s, "u.png", "image/png", b, 1000)
	sent := putAll(t, s, "s.png", "image/png", b, 1000)
	if _, err := s.Claim([]string{sent.ID}); err != nil {
		t.Fatal(err)
	}
	*now = now.Add(UploadTTL + time.Minute)
	s.Sweep()
	if _, err := os.Stat(s.sub("uploads", open.Upload+".part")); err == nil {
		t.Fatal("an upload not finished in an hour stays")
	}
	if _, err := s.Lookup(unsent.ID); err != nil {
		t.Fatal(err)
	}
	*now = now.Add(UnsentTTL)
	s.Sweep()
	if _, err := s.Lookup(unsent.ID); err == nil {
		t.Fatal("an image no message names stays past a day")
	}
	if _, err := s.Lookup(sent.ID); err != nil {
		t.Fatal("an image a message names goes within a day")
	}
	*now = now.Add(KeepFor)
	s.Sweep()
	if _, err := s.Lookup(sent.ID); err == nil {
		t.Fatal("an image stays past its 30 days")
	}
}
