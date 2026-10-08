// Package structured runs an agent as a Claude CLI in stream-json mode (the Agent SDK's wire protocol): one JSON object per line on
// stdin and stdout. A message goes in as a `user` object, a permission prompt comes out as a `can_use_tool` control request and is
// answered by a callback, a turn is interrupted by a control request. No screen is read and no key is typed.
//
// The protocol is documented by the SDK (@anthropic-ai/claude-agent-sdk, sdk.d.ts: SDKUserMessage, SDKControlRequest,
// SDKControlResponse); several control types are alpha, so the CLI version is pinned (PinnedCLI) and checked before a start.
package structured

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"
)

// PinnedCLI is the Claude CLI version the control types here were read against.
const PinnedCLI = "2.1.294"

// Delivery says what a message does when a turn is running: Now steers it into the running turn (priority "now"); Queue holds it
// until the turn ends.
type Delivery string

const (
	Now   Delivery = "now"
	Queue Delivery = "queue"
)

// State of a send: Queued (held behind a turn), Sent (written to the CLI), Confirmed (the CLI echoed it back by uuid).
type State string

const (
	Queued    State = "queued"
	Sent      State = "sent"
	Confirmed State = "confirmed"
)

// Receipt is the recorded answer of one operation; a retry with the same operation id gets it back and writes nothing.
type Receipt struct {
	Op    string
	UUID  string
	State State
}

// Decision answers a permission prompt. A zero Decision denies.
type Decision struct {
	Allow   bool
	Message string // why it was denied, told to the model
}

// ToolRequest is a `can_use_tool` control request: the CLI asks before running a tool.
type ToolRequest struct {
	ToolName string
	ToolUse  string
	Input    map[string]any
	Reason   string
}

// Handlers are the session's callbacks. CanUseTool is called on its own goroutine; ctx ends if the CLI cancels the prompt or the
// session closes. With no CanUseTool, or one that errors or runs out of time, the prompt is denied.
type Handlers struct {
	CanUseTool func(ctx context.Context, r ToolRequest) (Decision, error)
	OnEvent    func(ev map[string]any)
	OnExit     func(err error)
}

// Launch is how the CLI is started. ConfigDir is the account's CLAUDE_CONFIG_DIR; the child sees nothing of the daemon's
// environment beyond PATH, HOME, TMPDIR and LANG.
type Launch struct {
	Path      string
	Args      []string // extra arguments, after the protocol's own
	Cwd       string
	ConfigDir string
	SessionID string
	Model     string
}

// Args are the protocol's arguments: stream-json both ways, the user messages replayed for confirmation, permission prompts on stdio.
func (l Launch) args() []string {
	a := []string{"--print", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
		"--replay-user-messages", "--permission-prompt-tool", "stdio"}
	if l.SessionID != "" {
		a = append(a, "--session-id", l.SessionID)
	}
	if l.Model != "" {
		a = append(a, "--model", l.Model)
	}
	return append(a, l.Args...)
}

func (l Launch) env() []string {
	var env []string
	for _, k := range []string{"PATH", "HOME", "TMPDIR", "LANG"} {
		if v, ok := os.LookupEnv(k); ok {
			env = append(env, k+"="+v)
		}
	}
	if l.ConfigDir != "" {
		env = append(env, "CLAUDE_CONFIG_DIR="+l.ConfigDir)
	}
	return env
}

// CheckVersion refuses a CLI that is not the pinned one.
func CheckVersion(ctx context.Context, path, want string) error {
	out, err := exec.CommandContext(ctx, path, "--version").Output()
	if err != nil {
		return fmt.Errorf("claude --version: %w", err)
	}
	if got := strings.Fields(string(out)); len(got) == 0 || got[0] != want {
		return fmt.Errorf("claude is %q, the stream-json control types were read against %s", strings.TrimSpace(string(out)), want)
	}
	return nil
}

// Session is one running CLI.
type Session struct {
	cmd *exec.Cmd
	in  io.WriteCloser
	h   Handlers

	wmu sync.Mutex // one writer at a time

	mu       sync.Mutex
	ledger   map[string]*entry // operation id -> its send
	held     []*entry          // queued behind the running turn, oldest first
	byUUID   map[string]*entry
	echoing  int  // written messages whose echo has not come: an idle seen before it belongs to an earlier turn
	busy     bool // a turn is running or about to
	nextReq  int
	waiting  map[string]chan map[string]any // request_id -> the control response
	prompts  map[string]context.CancelFunc  // request_id -> a permission prompt in flight
	closed   bool
	exitErr  error
	done     chan struct{}
	initDone chan struct{}
}

type entry struct {
	op, uuid, text string
	state          State
	settled        chan struct{} // closed on Confirmed
}

// uuidFor is a valid UUID that is the same for the same operation id, so the CLI's echo names the operation.
func uuidFor(op string) string {
	h := sha256.Sum256([]byte("handline-operation:" + op))
	b := h[:16]
	b[6] = b[6]&0x0f | 0x50
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// Open starts the CLI and completes the initialize handshake; it fails closed when the CLI does not answer within timeout.
func Open(ctx context.Context, l Launch, h Handlers, timeout time.Duration) (*Session, error) {
	cmd := exec.Command(l.Path, l.args()...)
	cmd.Dir, cmd.Env = l.Cwd, l.env()
	in, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	out, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("start claude: %w", err)
	}
	s := &Session{cmd: cmd, in: in, h: h, ledger: map[string]*entry{}, byUUID: map[string]*entry{}, waiting: map[string]chan map[string]any{},
		prompts: map[string]context.CancelFunc{}, done: make(chan struct{}), initDone: make(chan struct{})}
	go s.read(out)
	ictx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	if _, err := s.control(ictx, map[string]any{"subtype": "initialize"}); err != nil {
		s.Close()
		return nil, fmt.Errorf("claude did not complete initialize: %w", err)
	}
	return s, nil
}

func (s *Session) write(v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	s.wmu.Lock()
	defer s.wmu.Unlock()
	_, err = s.in.Write(append(b, '\n'))
	return err
}

// control sends a control request and waits for its response.
func (s *Session) control(ctx context.Context, req map[string]any) (map[string]any, error) {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return nil, errors.New("session closed")
	}
	s.nextReq++
	id := fmt.Sprintf("req_%d", s.nextReq)
	ch := make(chan map[string]any, 1)
	s.waiting[id] = ch
	s.mu.Unlock()
	defer func() { s.mu.Lock(); delete(s.waiting, id); s.mu.Unlock() }()
	if err := s.write(map[string]any{"type": "control_request", "request_id": id, "request": req}); err != nil {
		return nil, err
	}
	select {
	case resp := <-ch:
		if resp["subtype"] == "error" {
			return nil, fmt.Errorf("claude refused %v: %v", req["subtype"], resp["error"])
		}
		r, _ := resp["response"].(map[string]any)
		return r, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-s.done:
		return nil, s.exitError()
	}
}

func (s *Session) exitError() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.exitErr != nil {
		return s.exitErr
	}
	return errors.New("claude exited")
}

// Send delivers text. The same op twice writes once and answers with the first receipt. With Queue while a turn runs, the message is
// held and written when the turn ends.
func (s *Session) Send(op, text string, d Delivery) (Receipt, error) {
	if op == "" {
		return Receipt{}, errors.New("a send needs an operation id")
	}
	s.mu.Lock()
	if e, ok := s.ledger[op]; ok {
		r := Receipt{e.op, e.uuid, e.state}
		s.mu.Unlock()
		return r, nil
	}
	if s.closed {
		s.mu.Unlock()
		return Receipt{}, errors.New("session closed")
	}
	e := &entry{op: op, uuid: uuidFor(op), text: text, state: Queued, settled: make(chan struct{})}
	s.ledger[op], s.byUUID[e.uuid] = e, e
	if d == Queue && s.busy {
		s.held = append(s.held, e)
		r := Receipt{e.op, e.uuid, e.state}
		s.mu.Unlock()
		return r, nil
	}
	s.markWritten(e)
	s.mu.Unlock()
	if err := s.writeUser(e, d); err != nil {
		s.mu.Lock()
		delete(s.ledger, op)
		delete(s.byUUID, e.uuid)
		s.mu.Unlock()
		return Receipt{}, err
	}
	return Receipt{e.op, e.uuid, Sent}, nil
}

// markWritten records that e is about to be written. Caller holds s.mu.
func (s *Session) markWritten(e *entry) {
	e.state = Sent
	s.echoing++
	s.busy = true
}

func (s *Session) writeUser(e *entry, d Delivery) error {
	m := map[string]any{"type": "user", "uuid": e.uuid, "parent_tool_use_id": nil,
		"message": map[string]any{"role": "user", "content": e.text}}
	if d == Now {
		m["priority"] = "now"
	}
	return s.write(m)
}

// WaitConfirmed waits until the CLI has echoed the operation's message, the delivery's confirmation.
func (s *Session) WaitConfirmed(ctx context.Context, op string) error {
	s.mu.Lock()
	e, ok := s.ledger[op]
	s.mu.Unlock()
	if !ok {
		return errors.New("no such operation")
	}
	select {
	case <-e.settled:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	case <-s.done:
		return s.exitError()
	}
}

// Interrupt stops the running turn.
func (s *Session) Interrupt(ctx context.Context) error {
	_, err := s.control(ctx, map[string]any{"subtype": "interrupt"})
	return err
}

// Busy says whether a turn is running or a written message is not yet echoed.
func (s *Session) Busy() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.busy
}

// Close ends stdin, which ends the CLI, and kills it if it does not go.
func (s *Session) Close() error {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return nil
	}
	s.closed = true
	for _, cancel := range s.prompts {
		cancel()
	}
	s.mu.Unlock()
	s.in.Close()
	select {
	case <-s.done:
	case <-time.After(3 * time.Second):
		s.cmd.Process.Kill()
		<-s.done
	}
	return nil
}

func (s *Session) read(out io.Reader) {
	sc := bufio.NewScanner(out)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	for sc.Scan() {
		var m map[string]any
		if json.Unmarshal(sc.Bytes(), &m) != nil {
			continue
		}
		s.handle(m)
	}
	err := s.cmd.Wait()
	s.mu.Lock()
	if s.exitErr == nil {
		if err == nil {
			err = errors.New("claude exited")
		}
		s.exitErr = err
	}
	s.mu.Unlock()
	close(s.done)
	if s.h.OnExit != nil {
		s.h.OnExit(err)
	}
}

func (s *Session) handle(m map[string]any) {
	switch m["type"] {
	case "control_response":
		r, _ := m["response"].(map[string]any)
		id, _ := r["request_id"].(string)
		s.mu.Lock()
		ch := s.waiting[id]
		s.mu.Unlock()
		if ch != nil {
			ch <- r
		}
	case "control_request":
		s.inbound(m)
		return
	case "control_cancel_request":
		id, _ := m["request_id"].(string)
		s.mu.Lock()
		cancel := s.prompts[id]
		s.mu.Unlock()
		if cancel != nil {
			cancel()
		}
	case "user":
		if uid, _ := m["uuid"].(string); uid != "" {
			s.echoed(uid)
		}
	case "system":
		if m["subtype"] == "session_state_changed" {
			s.stateChanged(m["state"])
		}
	}
	if s.h.OnEvent != nil {
		s.h.OnEvent(m)
	}
}

func (s *Session) echoed(uid string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if e := s.byUUID[uid]; e != nil && e.state == Sent {
		e.state = Confirmed
		s.echoing--
		close(e.settled)
	}
}

func (s *Session) stateChanged(state any) {
	s.mu.Lock()
	switch state {
	case "running", "requires_action":
		s.busy = true
		s.mu.Unlock()
		return
	}
	if s.echoing > 0 { // an idle that came before our message was echoed ended an earlier turn
		s.mu.Unlock()
		return
	}
	s.busy = false
	var next *entry
	if len(s.held) > 0 {
		next, s.held = s.held[0], s.held[1:]
		s.markWritten(next)
	}
	s.mu.Unlock()
	if next != nil {
		if err := s.writeUser(next, Queue); err != nil {
			s.mu.Lock()
			next.state = Queued
			s.echoing--
			s.held = append([]*entry{next}, s.held...)
			s.mu.Unlock()
		}
	}
}

// inbound answers a control request the CLI makes. Only can_use_tool is served; anything else is refused, so the CLI never waits.
func (s *Session) inbound(m map[string]any) {
	id, _ := m["request_id"].(string)
	req, _ := m["request"].(map[string]any)
	if req["subtype"] != "can_use_tool" {
		s.respondError(id, fmt.Sprintf("unsupported control request %v", req["subtype"]))
		return
	}
	tr := ToolRequest{Input: map[string]any{}}
	tr.ToolName, _ = req["tool_name"].(string)
	tr.ToolUse, _ = req["tool_use_id"].(string)
	tr.Reason, _ = req["decision_reason"].(string)
	if in, ok := req["input"].(map[string]any); ok {
		tr.Input = in
	}
	ctx, cancel := context.WithCancel(context.Background())
	s.mu.Lock()
	s.prompts[id] = cancel
	s.mu.Unlock()
	go func() {
		defer func() { cancel(); s.mu.Lock(); delete(s.prompts, id); s.mu.Unlock() }()
		d := Decision{Message: "no one answered the permission prompt"}
		if s.h.CanUseTool != nil {
			got, err := s.h.CanUseTool(ctx, tr)
			if err == nil {
				d = got
			} else {
				d.Message = "the permission prompt failed: " + err.Error()
			}
		}
		if ctx.Err() != nil { // cancelled by the CLI or by close: nothing to answer
			return
		}
		body := map[string]any{"behavior": "deny", "message": d.Message}
		if d.Allow {
			body = map[string]any{"behavior": "allow", "updatedInput": tr.Input}
		}
		s.write(map[string]any{"type": "control_response", "response": map[string]any{"subtype": "success", "request_id": id, "response": body}})
	}()
}

func (s *Session) respondError(id, msg string) {
	s.write(map[string]any{"type": "control_response", "response": map[string]any{"subtype": "error", "request_id": id, "error": msg}})
}
