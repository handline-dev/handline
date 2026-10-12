package chatwrites

import (
	"fmt"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/attachments"
)

func putImage(t *testing.T, r *rig) *attachments.Info {
	t.Helper()
	b := []byte("\x89PNG\r\n\x1a\n" + strings.Repeat("x", 2000))
	out, err := attachments.New(r.home).Put(attachments.PutIn{Name: "shot.png", Mime: "image/png", Bytes: int64(len(b)), Data: b})
	if err != nil || out.Attachment == nil {
		t.Fatal(err)
	}
	return out.Attachment
}

func TestSendNamesTheImagesAndTheMessageKeepsThem(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	img := putImage(t, r)
	res := r.result(r.one(s, "chat.send", fmt.Sprintf(`{"agent":"alice","body":"look","attachments":[%q],"request_id":"a1"}`, img.ID)))
	rid := int64(res["request"].(float64))
	got, err := attachments.Of(r.db, rid)
	if err != nil || len(got) != 1 || got[0] != *img {
		t.Fatalf("the message carries %v %v", got, err)
	}
	line := attachments.New(r.home).Lines(r.db, rid)
	want := fmt.Sprintf("[image 1: %s/attachments/%s.png (image/png, 2 KB)]", r.home, img.ID)
	if line != want {
		t.Fatalf("path line %q, want %q", line, want)
	}
	var body string
	r.db.QueryRow("SELECT body FROM requests WHERE id = ?", rid).Scan(&body)
	if body != "look" {
		t.Fatalf("the body stays as typed: %q", body)
	}
}

func TestImagesMayBeTheWholeMessage(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	img := putImage(t, r)
	r.result(r.one(s, "chat.send", fmt.Sprintf(`{"agent":"alice","attachments":[%q],"request_id":"a2"}`, img.ID)))
	e, _ := r.one(s, "chat.send", `{"agent":"alice","request_id":"a3"}`)["error"].(map[string]any)
	if e["data"].(map[string]any)["field"] != "body" {
		t.Fatalf("no body and no image: %v", e)
	}
}

func TestSendRefusesWhatIsNotAnImageList(t *testing.T) {
	r := newRig(t)
	s := r.server("")
	img := putImage(t, r)
	for i, params := range []string{
		`"attachments":[]`,
		`"attachments":"x"`,
		`"attachments":[1]`,
		`"attachments":["../x"]`,
		`"attachments":["0000000000000001","0000000000000002","0000000000000003","0000000000000004","0000000000000005","0000000000000006","0000000000000007"]`,
		fmt.Sprintf(`"task":{"title":"t"},"attachments":[%q]`, img.ID),
	} {
		e, _ := r.one(s, "chat.send", fmt.Sprintf(`{"agent":"alice","body":"x",%s,"request_id":"b%d"}`, params, i))["error"].(map[string]any)
		if e == nil || e["data"].(map[string]any)["field"] != "attachments" {
			t.Fatalf("%s: %v", params, e)
		}
	}
	e, _ := r.one(s, "chat.send", `{"agent":"alice","body":"x","attachments":["0000000000000000"],"request_id":"c1"}`)["error"].(map[string]any)
	if e["data"].(map[string]any)["code"] != "not_found" {
		t.Fatalf("an image that is not there: %v", e)
	}
	if n := r.count("SELECT COUNT(*) FROM requests"); n != 0 {
		t.Fatalf("a refused send wrote %d requests", n)
	}
}
