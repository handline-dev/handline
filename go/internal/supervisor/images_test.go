package supervisor

import (
	"encoding/base64"
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/attachments"
	"github.com/handline/handline/go/internal/db"
)

func TestAnAgentIsToldWhereTheImagesOfATasksTextsAre(t *testing.T) {
	r := newRig(t)
	r.agentUp("bob")
	s := attachments.New(r.home)
	png, _ := base64.StdEncoding.DecodeString("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
	put := func() attachments.Info {
		out, err := s.Put(r.DB, attachments.PutIn{Name: "a.png", Mime: "image/png", Bytes: int64(len(png)), Data: png})
		if err != nil || out.Attachment == nil {
			t.Fatalf("put: %v %v", out, err)
		}
		return *out.Attachment
	}
	brief, memo := put(), put()
	line := func(n int, i attachments.Info) string {
		return attachments.Line(n, s.Path(i), "image/png", int64(len(png)))
	}
	tid := r.newTask("bob", "alice", "job")
	_, err := db.AddMemo(r.DB, tid, "alice", "this one ![m](attachment:"+memo.ID+")")
	r.must(err)
	rid := r.ask("alice", "bob", "fix it ![b](attachment:"+brief.ID+")", "task")
	r.must(db.LinkRequest(r.DB, tid, rid))
	if got := r.imageLines(r.DB, mustReq(r, rid)); got != line(1, brief)+"\n"+line(2, memo) {
		t.Fatalf("the brief's image, then its memos': %q", got)
	}
	if got := r.imageLines(r.DB, mustReq(r, r.ask("alice", "bob", "![b](attachment:"+brief.ID+")", "say"))); got != line(1, brief) {
		t.Fatalf("a say names images too: %q", got)
	}
	r.AdvanceCurrentTask(r.DB, "bob")
	r.term.typed = nil
	r.must(r.TellMemo(r.DB, tid, "alice", "added", strings.Repeat("w", 420)+" ![m](attachment:"+memo.ID+")", false))
	if !strings.Contains(r.term.last(), line(1, memo)) {
		t.Fatalf("a long memo still names its image: %q", r.term.last())
	}
}
