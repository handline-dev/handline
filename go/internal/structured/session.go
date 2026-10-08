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
	"syscall"
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

// State of a send: Queued (held behind a turn), Sent (written to the CLI), Confirmed (the CLI echoed it back by uuid, or reported it queued or started).
type State string

const (
	Queued    State = "queued"
	Sent      State = "sent"
	Confirmed State = "confirmed"
	// Unknown: the message was written, and the daemon or the CLI died before the echo. The CLI may or may not have run it, so it is
	// never written again; the sender is told.
	Unknown State = "unknown"
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
// environment beyond PATH, HOME, USER, LOGNAME (the CLI finds the account's login by user name), TMPDIR and LANG.
type Launch struct {
	Path      string
	Args      []string // extra arguments, after the protocol's own
	Cwd       string
	ConfigDir string
	SessionID string
	Resume    bool // SessionID is an existing session: --resume instead of --session-id
	Model     string
	Env       []string         // NAME=value pairs the daemon sets on purpose (the agent's name, say), after the allowlisted ones
	Now       func() time.Time // the clock for RetryingSince; nil is time.Now
	Ledger    Ledger           // where sends are recorded before they are written; nil keeps them in memory
}

// Args are the protocol's arguments: stream-json both ways, the user messages replayed for confirmation, permission prompts on stdio.
func (l Launch) args() []string {
	a := []string{"--print", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
		"--replay-user-messages", "--permission-prompt-tool", "stdio"}
	switch {
	case l.SessionID != "" && l.Resume:
		a = append(a, "--resume", l.SessionID)
	case l.SessionID != "":
		a = append(a, "--session-id", l.SessionID)
	}
	if l.Model != "" {
		a = append(a, "--model", l.Model)
	}
	return append(a, l.Args...)
}

func (l Launch) env() []string {
	var env []string
	for _, k := range []string{"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG"} {
		if v, ok := os.LookupEnv(k); ok {
			env = append(env, k+"="+v)
		}
	}
	// Without this the CLI sends no session_state_changed frames (seen on the real CLI, live_test.go).
	env = append(env, "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1")
	if l.ConfigDir != "" {
		env = append(env, "CLAUDE_CONFIG_DIR="+l.ConfigDir)
	}
	return append(env, l.Env...)
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
	led Ledger
	now func() time.Time

	wmu sync.Mutex // one writer at a time

	mu       sync.Mutex
	ledger   map[string]*entry // operation id -> its send
	held     []*entry          // queued behind the running turn, oldest first
	byUUID   map[string]*entry
	echoing  int    // written messages whose echo has not come: an idle seen before it belongs to an earlier turn
	retrying int64  // unix seconds the current run of api_retry frames began; 0 when the model call is not being retried
	state    string // the CLI's last session_state_changed: idle, running, requires_action; "" before the first
	busy     bool   // a turn is running or about to
	nextReq  int
	waiting  map[string]chan map[string]any // request_id -> the control response
	prompts  map[string]context.CancelFunc  // request_id -> a permission prompt in flight
	closed   bool
	closeMu  sync.Mutex // one Close at a time; a repeat proves the group again
	exitErr  error
	done     chan struct{}
	initDone chan struct{}
}

type entry struct {
	op, uuid, text string
	delivery       Delivery
	state          State
	settled        chan struct{} // closed when the state is settled: Confirmed, or Unknown
	timer          *time.Timer   // the echo deadline while Sent
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
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} // the CLI and what it starts are one group, so close can prove they are gone
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
	led := l.Ledger
	if led == nil {
		led = &MemLedger{}
	}
	now := l.Now
	if now == nil {
		now = time.Now
	}
	s := &Session{cmd: cmd, in: in, h: h, led: led, now: now, ledger: map[string]*entry{}, byUUID: map[string]*entry{}, waiting: map[string]chan map[string]any{},
		prompts: map[string]context.CancelFunc{}, done: make(chan struct{}), initDone: make(chan struct{})}
	go s.read(out)
	ictx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	if _, err := s.control(ictx, map[string]any{"subtype": "initialize"}); err != nil {
		s.Close()
		return nil, fmt.Errorf("claude did not complete initialize: %w", err)
	}
	if err := s.recover(); err != nil {
		s.Close()
		return nil, err
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

// Send delivers text. The same op twice writes once and answers with the first receipt, also after a restart: the ledger has it. With
// Queue while a turn runs, the message is held and written when the turn ends. The operation is recorded before it is written; if it
// cannot be recorded it is not written.
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
	prior, err := s.led.Get(op)
	if err != nil {
		s.mu.Unlock()
		return Receipt{}, err
	}
	if prior != nil {
		s.mu.Unlock()
		return Receipt{prior.ID, prior.UUID, prior.State}, nil
	}
	if s.closed {
		s.mu.Unlock()
		return Receipt{}, errors.New("session closed")
	}
	e := &entry{op: op, uuid: uuidFor(op), text: text, delivery: d, state: Queued, settled: make(chan struct{})}
	held := d == Queue && s.busy
	if !held {
		e.state = Sent
	}
	if err := s.led.Put(e.record()); err != nil {
		s.mu.Unlock()
		return Receipt{}, err
	}
	s.ledger[op], s.byUUID[e.uuid] = e, e
	if held {
		s.held = append(s.held, e)
		r := Receipt{e.op, e.uuid, e.state}
		s.mu.Unlock()
		return r, nil
	}
	wasBusy := s.busy
	s.markWritten(e)
	s.mu.Unlock()
	if err := s.writeUser(e, d); err != nil {
		// A partial write may have reached the CLI, so the message is Unknown and is not retried; the counts it took are given back.
		s.unknown(e)
		s.mu.Lock()
		s.busy = wasBusy
		s.mu.Unlock()
		return Receipt{}, err
	}
	return Receipt{e.op, e.uuid, Sent}, nil
}

func (e *entry) record() Op {
	return Op{ID: e.op, UUID: e.uuid, Text: e.text, Delivery: e.delivery, State: e.state}
}

// recover settles the ledger of an earlier process: a message written and never echoed is Unknown and is not written again; a
// message still queued is held again and goes out once the CLI is idle.
func (s *Session) recover() error {
	pending, err := s.led.Pending()
	if err != nil {
		return err
	}
	s.mu.Lock()
	for _, o := range pending {
		e := &entry{op: o.ID, uuid: o.UUID, text: o.Text, delivery: o.Delivery, state: o.State, settled: make(chan struct{})}
		if o.State == Sent {
			e.state = Unknown
			if err := s.led.Put(e.record()); err != nil {
				s.mu.Unlock()
				return err
			}
			continue
		}
		s.ledger[o.ID], s.byUUID[o.UUID] = e, e
		s.held = append(s.held, e)
	}
	s.mu.Unlock()
	s.releaseNext()
	return nil
}

// markWritten records that e is about to be written. Caller holds s.mu.
func (s *Session) markWritten(e *entry) {
	e.state = Sent
	s.echoing++
	s.busy = true
	e.timer = time.AfterFunc(EchoDeadline, func() { s.lose(e) })
}

// EchoDeadline is how long a written message may go without the CLI echoing it or reporting it queued or started. After it the
// message is Unknown: it is not written again, the sender is told, and the next message is not held behind it.
var EchoDeadline = 2 * time.Minute

// lose settles a message that was written and never acknowledged.
func (s *Session) lose(e *entry) { s.unknown(e) }

// unknown turns a Sent or Confirmed message into Unknown, once. Caller does not hold s.mu.
func (s *Session) unknown(e *entry) {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch e.state {
	case Sent:
		s.echoing--
		close(e.settled)
	case Confirmed:
	default:
		return
	}
	if e.timer != nil {
		e.timer.Stop()
	}
	e.state = Unknown
	s.led.Put(e.record())
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
		s.mu.Lock()
		defer s.mu.Unlock()
		if e.state != Confirmed {
			return errors.New("the message is unknown: the CLI never acknowledged it")
		}
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

// PromptTimeout is the longest a permission callback may take; past it the prompt is denied. The broker's own timeout is shorter.
var PromptTimeout = 2 * time.Hour

// CloseGrace is how long Close waits for the CLI to go after its stdin ends, before it kills the process group.
var CloseGrace = 3 * time.Second

// Close ends stdin, which ends the CLI, kills the CLI's process group if it does not go, and returns only once no process of the
// group is left. An error means one is still alive: the caller must not report the agent stopped.
func (s *Session) Close() error {
	s.closeMu.Lock()
	defer s.closeMu.Unlock()
	s.mu.Lock()
	first := !s.closed
	s.closed = true
	if first {
		for _, cancel := range s.prompts {
			cancel()
		}
	}
	s.mu.Unlock()
	s.in.Close()
	pgid := s.cmd.Process.Pid // Setpgid made the CLI the leader of its own group
	select {
	case <-s.done:
	case <-time.After(CloseGrace):
		syscall.Kill(-pgid, syscall.SIGKILL)
		<-s.done
	}
	// Every call proves the group again, so a Close that failed once is not reported as done by the next.
	return proveGone(pgid)
}

// proveGone is groupGone; a test replaces it.
var proveGone = groupGone

// groupGone proves the process group is empty, killing what is left of it: the CLI's children outlive it when it dies without
// reaping them.
func groupGone(pgid int) error {
	for i := 0; i < 50; i++ {
		if err := syscall.Kill(-pgid, 0); errors.Is(err, syscall.ESRCH) {
			return nil
		}
		syscall.Kill(-pgid, syscall.SIGKILL)
		time.Sleep(20 * time.Millisecond)
	}
	return fmt.Errorf("processes of the claude group %d are still running", pgid)
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
	s.noteRetry(m)
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
	case "command_lifecycle":
		// The CLI has taken the message (msg_lifecycle_v1): queued or started, named by command_uuid. Seen on every real turn, also
		// when the model call then failed and the isReplay echo never came.
		uid, _ := m["command_uuid"].(string)
		switch m["state"] {
		case "queued", "started":
			if uid != "" {
				s.echoed(uid)
			}
		case "cancelled": // queued and then dropped (an interrupt, an exit): it may not have run, and the sender is told
			s.mu.Lock()
			e := s.byUUID[uid]
			s.mu.Unlock()
			if e != nil {
				s.unknown(e)
			}
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
		if e.timer != nil {
			e.timer.Stop()
		}
		s.led.Put(e.record())
		close(e.settled)
	}
}

func (s *Session) stateChanged(state any) {
	s.mu.Lock()
	s.state, _ = state.(string)
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
	s.mu.Unlock()
	s.releaseNext()
}

// releaseNext writes the oldest held message if no turn is running. The held message is recorded Sent before it is written.
func (s *Session) releaseNext() {
	s.mu.Lock()
	if s.busy || s.echoing > 0 || s.closed || len(s.held) == 0 {
		s.mu.Unlock()
		return
	}
	next := s.held[0]
	s.markWritten(next)
	if err := s.led.Put(next.record()); err != nil {
		next.state = Queued
		s.echoing--
		s.busy = false
		s.mu.Unlock()
		return
	}
	s.held = s.held[1:]
	s.mu.Unlock()
	if err := s.writeUser(next, Queue); err != nil {
		s.unknown(next)
		s.mu.Lock()
		s.busy = false
		s.mu.Unlock()
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
			type result struct {
				d   Decision
				err error
			}
			rc := make(chan result, 1)
			go func() { got, err := s.h.CanUseTool(ctx, tr); rc <- result{got, err} }()
			select {
			case r := <-rc:
				if r.err == nil {
					d = r.d
				} else {
					d.Message = "the permission prompt failed: " + r.err.Error()
				}
			case <-time.After(PromptTimeout): // a handler that ignores ctx cannot hold the CLI for ever
				d.Message = "the permission prompt was not answered in time"
			case <-ctx.Done():
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

// State is the CLI's last reported session state: idle, running or requires_action; "" until it has reported one.
func (s *Session) State() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.state
}

// Done is closed when the CLI process has exited; ExitErr is then why. Liveness is this, not a screen.
func (s *Session) Done() <-chan struct{} { return s.done }

func (s *Session) ExitErr() error {
	select {
	case <-s.done:
		return s.exitError()
	default:
		return nil
	}
}

// Pid is the CLI's process id, which is also its process group.
func (s *Session) Pid() int { return s.cmd.Process.Pid }

// noteRetry tracks a run of api_retry frames: the CLI retries a failed model call for minutes, and a turn that only retries is
// stalled. Any other frame of the turn (output, a result, a state change) ends the run.
func (s *Session) noteRetry(m map[string]any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch {
	case m["type"] == "system" && m["subtype"] == "api_retry":
		if s.retrying == 0 {
			s.retrying = s.now().Unix()
		}
	case m["type"] == "assistant" || m["type"] == "result" || m["type"] == "stream_event" ||
		(m["type"] == "system" && m["subtype"] == "session_state_changed"):
		s.retrying = 0
	}
}

// RetryingSince is when the current run of api_retry frames began (unix seconds), or 0 when the model call is not being retried.
func (s *Session) RetryingSince() int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.retrying
}
