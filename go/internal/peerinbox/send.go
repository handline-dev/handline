package peerinbox

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

// Kind is how a delivery ended.
type Kind int

const (
	Delivered    Kind = iota // the receiver's transcript shows the message as a peer's user record
	NotDelivered             // sent, or not, but never seen there; Why says which
	NoSocket                 // nothing listens: the session is gone or has no inbox
	Queued                   // the receiver has it queued behind a turn or a prompt and shows it when that ends; Result.Await watches for it
)

func (k Kind) String() string {
	return [...]string{"delivered", "not-delivered", "no-socket", "queued"}[k]
}

// Result is the outcome of Deliver. ID is the request id the message carried; Attempts is how many times it was written.
type Result struct {
	Kind     Kind
	Why      string
	ID       string
	Attempts int
	// Await, set on Queued, waits up to QueuedWait for the transcript to show the message and says whether it did.
	Await func(ctx context.Context) bool
}

// Sender writes to peer inboxes. The zero value is not usable; use New. Its fields may be set before the first Deliver.
type Sender struct {
	Dial func(socket string) (net.Conn, error)
	// Gap is the least time between two writes to one target (the receiver refills 0.5 tokens a second).
	Gap time.Duration
	// ConfirmWait is how long one write is given to show in the transcript; ResendAfter is the pause before it is written again.
	ConfirmWait time.Duration
	ResendAfter time.Duration
	Poll        time.Duration
	// Tries is the most writes of one message.
	Tries int
	// QueuedWait is how long a queued message is watched for.
	QueuedWait time.Duration
	// Refresh reads the session's record again; a message is not written again while the session waits on a person.
	Refresh func(Session) (Session, error)

	Now   func() time.Time
	Sleep func(ctx context.Context, d time.Duration) error
	NewID func() string

	mu   sync.Mutex
	last map[int]time.Time
}

// New is a Sender with the pacing the receiver needs.
func New() *Sender {
	return &Sender{
		Dial:        func(s string) (net.Conn, error) { return net.DialTimeout("unix", s, 3*time.Second) },
		Gap:         2 * time.Second,
		ConfirmWait: 10 * time.Second,
		ResendAfter: 5 * time.Second,
		Poll:        500 * time.Millisecond,
		Tries:       2,
		QueuedWait:  30 * time.Minute,
		Refresh:     func(s Session) (Session, error) { return ByPID(s.Config, s.PID) },
		Now:         time.Now,
		Sleep:       sleep,
		NewID:       randomID,
		last:        map[int]time.Time{},
	}
}

func sleep(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return ctx.Err()
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

func randomID() string {
	var b [8]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

// Body is the text a session receives: the message, then the request id the transcript is searched for. The id also keeps two
// equal messages from being dropped as a duplicate.
func Body(text, id string) string { return text + "\n[ocp-msg " + id + "]" }

// The receiver reads who a cross-session message is from from the tag the content is wrapped in. Without the tag it is still another
// session's (Claude Code 2.1.303 wraps it so itself): the person's own prompt can only be typed (Type).
const (
	envelopeTag = "cross-session-message"
	senderName  = "handline"
)

// From says who a message is from: Name is shown with it (the daemon, "handline", when empty).
type From struct{ Name string }

var tagLookalike = regexp.MustCompile(`(?i)<([\p{Cf}\s]*/?[\p{Cf}\s]*` + envelopeTag + `)`)

// Envelope wraps body as the receiver reads a peer message that names its sender. The form is the receiver's canonical
// one: attributes in its order, and a tag of the same name inside body defanged as it does, since a wrapper it cannot read back exactly is taken
// as plain text.
func Envelope(body string, from From) string {
	name := senderName
	if n := strings.Join(strings.Fields(strings.NewReplacer(`"`, "", "<", "", ">", "").Replace(from.Name)), " "); n != "" {
		name = n
	}
	body = tagLookalike.ReplaceAllString(body, `<\$1`)
	attrs := ` from-session="` + senderName + `" from-name="` + name + `"`
	return "<" + envelopeTag + attrs + ">\n" + body + "\n</" + envelopeTag + ">"
}

// pace waits until target may be written to again, and claims the slot.
func (s *Sender) pace(ctx context.Context, pid int) error {
	for {
		s.mu.Lock()
		wait := s.Gap - s.Now().Sub(s.last[pid])
		if wait <= 0 {
			s.last[pid] = s.Now()
			s.mu.Unlock()
			return nil
		}
		s.mu.Unlock()
		if err := s.Sleep(ctx, wait); err != nil {
			return err
		}
	}
}

type userLine struct {
	Type    string      `json:"type"`
	Message userMessage `json:"message"`
}

type userMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// write sends the auth line, then one user line. The key lives in key for the length of the call and is zeroed after.
func (s *Sender) write(sess Session, body string) error {
	key, err := sess.readKey()
	if err != nil {
		return err
	}
	defer clear(key)
	conn, err := s.Dial(sess.Socket)
	if err != nil {
		return errDial
	}
	defer conn.Close()
	auth, err := authLine(key)
	if err != nil {
		return err
	}
	defer clear(auth)
	msg, err := json.Marshal(userLine{Type: "user", Message: userMessage{Role: "user", Content: body}})
	if err != nil {
		return err
	}
	_ = conn.SetWriteDeadline(time.Now().Add(5 * time.Second))
	if _, err := conn.Write(append(auth, '\n')); err != nil {
		return errWrite
	}
	if _, err := conn.Write(append(msg, '\n')); err != nil {
		return errWrite
	}
	return nil
}

// Errors of a write carry no text from the connection or the key.
var (
	errDial  = errors.New("cannot connect to the peer inbox socket")
	errWrite = errors.New("cannot write to the peer inbox socket")
)

func authLine(key []byte) ([]byte, error) {
	tok, err := json.Marshal(string(key))
	if err != nil {
		return nil, err
	}
	line := append([]byte(`{"type":"auth","token":`), tok...)
	clear(tok)
	return append(line, '}'), nil
}

// Deliver sends text to sess as from (Envelope), and waits until its transcript shows it. A write that does not show within ConfirmWait is written
// again with the same id after ResendAfter, up to Tries writes. Delivered needs the peer's user record; a message only queued
// behind a running turn or a prompt comes back Queued: it is not written again and not a failure.
func (s *Sender) Deliver(ctx context.Context, sess Session, text string, from From) Result {
	id := s.NewID()
	body := Envelope(Body(text, id), from)
	res := Result{ID: id}
	tr := newTail(sess)
	for res.Attempts < s.Tries {
		if res.Attempts > 0 {
			if err := s.Sleep(ctx, s.ResendAfter); err != nil {
				res.Kind, res.Why = NotDelivered, err.Error()
				return res
			}
			// The receiver queued it behind a prompt or a running turn: writing it again only adds a duplicate to the queue, which the person
			// then reads twice.
			if now, err := s.Refresh(sess); err == nil && now.Blocked() {
				res.Kind, res.Why = Queued, "the session waits on a person ("+blockedWord(now)+"); the message stays queued until it is answered"
				res.Await = s.awaiter(tr, peerRecord(id))
				return res
			} else if err == nil && now.Busy() {
				res.Kind, res.Why = Queued, "the session is mid-turn; the message waits in its queue and is not written again"
				res.Await = s.awaiter(tr, peerRecord(id))
				return res
			}
		}
		if err := s.pace(ctx, sess.PID); err != nil {
			res.Kind, res.Why = NotDelivered, err.Error()
			return res
		}
		res.Attempts++
		if err := s.write(sess, body); err != nil {
			res.Why = err.Error()
			switch {
			case err == errDial:
				res.Kind = NoSocket
			default:
				res.Kind = NotDelivered
			}
			if res.Kind == NoSocket || errors.Is(err, ErrNoKey) {
				return res
			}
			continue
		}
		seen, queued, err := s.wait(ctx, tr, peerRecord(id))
		if err != nil {
			res.Kind, res.Why = NotDelivered, err.Error()
			return res
		}
		if seen {
			res.Kind, res.Why = Delivered, ""
			return res
		}
		if queued {
			res.Kind, res.Why = Queued, "queued by the receiver, not yet shown as a message"
			res.Await = s.awaiter(tr, peerRecord(id))
			return res // it has the message: a second write is a second copy
		}
		res.Kind, res.Why = NotDelivered, "not in the receiver's transcript"
	}
	return res
}

// awaiter watches the transcript for match for QueuedWait.
func (s *Sender) awaiter(tr *tail, match matcher) func(ctx context.Context) bool {
	return func(ctx context.Context) bool {
		deadline := s.Now().Add(s.QueuedWait)
		for {
			if sn, _ := tr.scan(match); sn {
				return true
			}
			if !s.Now().Before(deadline) || s.Sleep(ctx, s.Poll) != nil {
				return false
			}
		}
	}
}

func blockedWord(s Session) string {
	for _, w := range []string{s.WaitingFor, s.Detail, s.State, s.Status} {
		if w != "" {
			return w
		}
	}
	return "blocked"
}

// wait polls the transcript for match up to ConfirmWait.
func (s *Sender) wait(ctx context.Context, tr *tail, match matcher) (seen, queued bool, err error) {
	deadline := s.Now().Add(s.ConfirmWait)
	for {
		sn, q := tr.scan(match)
		queued = queued || q
		if sn {
			return true, queued, nil
		}
		if !s.Now().Before(deadline) {
			return false, queued, nil
		}
		if err := s.Sleep(ctx, s.Poll); err != nil {
			return false, queued, err
		}
	}
}

// tail reads a session's transcript from where it ended when the delivery began.
type tail struct {
	sess   Session
	path   string
	offset int64
	rest   string
}

func newTail(sess Session) *tail {
	t := &tail{sess: sess}
	t.path = transcriptPath(sess)
	if t.path != "" {
		if fi, err := os.Stat(t.path); err == nil {
			t.offset = fi.Size()
		}
	}
	return t
}

// transcriptPath is <config>/projects/*/<sessionId>.jsonl; "" while none exists yet.
func transcriptPath(sess Session) string {
	if sess.SessionID == "" {
		return ""
	}
	m, _ := filepath.Glob(filepath.Join(sess.Config, "projects", "*", sess.SessionID+".jsonl"))
	if len(m) == 0 {
		return ""
	}
	return m[0]
}

// scan reads what was appended since the last scan: seen when match finds a record that shows the message, queued when it finds one
// that only queued it.
func (t *tail) scan(match matcher) (seen, queued bool) {
	if t.path == "" {
		if t.path = transcriptPath(t.sess); t.path == "" {
			return false, false
		}
	}
	f, err := os.Open(t.path)
	if err != nil {
		return false, false
	}
	defer f.Close()
	if _, err := f.Seek(t.offset, io.SeekStart); err != nil {
		return false, false
	}
	r := bufio.NewReader(f)
	for {
		line, err := r.ReadString('\n')
		t.offset += int64(len(line))
		if !strings.HasSuffix(line, "\n") {
			// A record being written: read it again next time.
			t.offset -= int64(len(line))
			return seen, queued
		}
		switch match(line) {
		case recSeen:
			seen = true
		case recQueued:
			queued = true
		}
		if err != nil {
			return seen, queued
		}
	}
}

type recKind int

const (
	recOther recKind = iota
	recQueued
	recSeen
)

// matcher says what one transcript line is to the message looked for.
type matcher func(line string) recKind

// entry is the part of a transcript line the matchers read.
type entry struct {
	Type       string                `json:"type"`
	Operation  string                `json:"operation"`
	Content    any                   `json:"content"`
	Origin     struct{ Kind string } `json:"origin"`
	Message    struct{ Content any } `json:"message"`
	Attachment struct {
		Type   string                `json:"type"`
		Prompt string                `json:"prompt"`
		Origin struct{ Kind string } `json:"origin"`
	} `json:"attachment"`
}

// peerRecord finds a message the inbox wrote under id: seen as a peer's user record, or a peer's queued_command attachment (taken into
// the running turn); queued by a queue-operation enqueue.
func peerRecord(id string) matcher {
	return func(line string) recKind {
		var r entry
		if !strings.Contains(line, id) || json.Unmarshal([]byte(line), &r) != nil {
			return recOther
		}
		switch {
		case r.Type == "queue-operation" && r.Operation == "enqueue":
			return recQueued
		case r.Type == "user" && r.Origin.Kind == "peer",
			r.Type == "attachment" && r.Attachment.Type == "queued_command" && r.Attachment.Origin.Kind == "peer":
			return recSeen
		}
		return recOther
	}
}

// ShowsTyped is whether a transcript line is text, typed at the session, as the session took it in.
func ShowsTyped(line, text string) bool { return typedRecord(text)(line) == recSeen }

// typedRecord finds text typed at the session: seen as the person's user record, or a queued_command attachment that is not a peer's
// (typed mid-turn, taken into it); queued by a queue-operation enqueue of it.
func typedRecord(text string) matcher {
	text = strings.TrimSpace(text)
	return func(line string) recKind {
		var r entry
		if json.Unmarshal([]byte(line), &r) != nil {
			return recOther
		}
		switch {
		case r.Type == "queue-operation" && r.Operation == "enqueue" && same(r.Content, text):
			return recQueued
		case r.Type == "user" && r.Origin.Kind == "human" && same(r.Message.Content, text),
			r.Type == "attachment" && r.Attachment.Type == "queued_command" && r.Attachment.Origin.Kind != "peer" && same(r.Attachment.Prompt, text):
			return recSeen
		}
		return recOther
	}
}

// pasted is how Claude Code records a multi-line paste it collapsed to "[Pasted text #1 +3 lines]": the text wrapped in a tag pair.
var pasted = regexp.MustCompile(`(?s)<pasted_content id="[^"]*">\n(.*?)\n</pasted_content id="[^"]*">`)

func same(v any, text string) bool {
	s, ok := v.(string)
	return ok && strings.TrimSpace(pasted.ReplaceAllString(s, "$1")) == text
}

// Type puts text into the session with typeIn and waits, as Deliver does, until the transcript shows it as the person's own prompt. It is
// the one delivery made of keystrokes: whatever the inbox carries reaches the receiver as another session's. Typed mid-turn, the receiver
// queues it and takes it into the running turn, so a queued one comes back Queued with Await, never typed again.
func (s *Sender) Type(ctx context.Context, sess Session, text string, typeIn func(text string) error) Result {
	tr := newTail(sess)
	res := Result{Attempts: 1}
	if err := typeIn(text); err != nil {
		res.Kind, res.Why, res.Attempts = NotDelivered, err.Error(), 0
		return res
	}
	match := typedRecord(text)
	seen, queued, err := s.wait(ctx, tr, match)
	switch {
	case err != nil:
		res.Kind, res.Why = NotDelivered, err.Error()
	case seen:
		res.Kind = Delivered
	case queued:
		res.Kind, res.Why, res.Await = Queued, "typed mid-turn; the session takes it in when it can", s.awaiter(tr, match)
	default:
		res.Kind, res.Why = NotDelivered, "typed, but not in the receiver's transcript"
	}
	return res
}
