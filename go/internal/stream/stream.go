// Package stream is the event stream (contract/protocol.md: subscribe, unsubscribe, event, gap). Every change a client may watch is a row
// in stream_log, whose id is the cursor, so a client that reconnects with its last cursor gets exactly what it missed.
//
// Rows come from any writer: this daemon's Append, and the Python daemon's watcher until it stops. One poll of MAX(id) (no detectors)
// hands every new row to the subscribers in cursor order, so a row is served the same whoever wrote it. The devices and transcript topics
// are not rows: they are fanned out live from memory with cursor 0, and a client that missed any reads devices.list or transcript.events.
// A client that cannot keep up is dropped with a gap, never buffered without limit.
package stream

import (
	"encoding/json"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

const (
	devicesTopic    = "devices"
	transcriptTopic = "transcript"
	// MaxQueue is how many live events may wait for one client before it is dropped with a gap.
	MaxQueue = 1000
	// PollEvery is how often the log is looked at for rows another writer appended.
	PollEvery  = 250 * time.Millisecond
	pruneEvery = time.Hour
	batch      = 1000
)

var topics = map[string]bool{"status": true, "accounts": true, "agents": true, "inbox": true, "tasks": true,
	"notifications": true, "operations": true, "chat": true, devicesTopic: true, "hr": true, transcriptTopic: true}

// Hub fans events out to the connections subscribed to them.
type Hub struct {
	// Reach is the agents an agent's own reach covers, for the tasks and notifications an agent session may be sent.
	Reach func(agent string) (map[string]bool, error)
	// Log is the stream_log; nil serves the devices and transcript topics alone.
	Log *Log

	mu   sync.Mutex
	subs map[*sub]struct{}
	top  int64 // the newest cursor handed to the subscribers
	wake chan struct{}
}

func NewHub() *Hub { return &Hub{subs: map[*sub]struct{}{}, wake: make(chan struct{}, 1)} }

func msgOf(ev *Event) []byte {
	b, err := json.Marshal(map[string]any{"jsonrpc": "2.0", "method": "event", "params": ev})
	if err != nil {
		return nil
	}
	return b
}

// publish hands one event to every subscriber that wants it.
func (h *Hub) publish(ev *Event) {
	msg := msgOf(ev)
	if msg == nil {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	for s := range h.subs {
		s.offer(ev, msg)
	}
}

// EmitTranscript sends a transcript event (a committed row, as transcript.events shows it) to the subscribers of the transcript topic
// that want its agent. Events are live only and carry cursor 0: a client that missed any reads transcript.events from its last seq.
func (h *Hub) EmitTranscript(agent string, ev map[string]any) {
	data, err := json.Marshal(ev)
	if err != nil {
		return
	}
	at, _ := ev["at"].(string)
	h.publish(&Event{Topic: transcriptTopic, Type: "transcript.event", At: at, Agent: &agent, Data: data})
}

// Emit sends a devices event to every subscriber. Events are live only and carry cursor 0: the stream's cursors
// are the log's, and a client resumes the devices topic from devices.list.
func (h *Hub) Emit(typ string, data, actor map[string]any, title string) {
	ev := &Event{Topic: devicesTopic, Type: typ, At: time.Now().UTC().Format("2006-01-02T15:04:05Z")}
	ev.Data, _ = json.Marshal(data)
	if actor != nil {
		ev.Actor, _ = json.Marshal(actor)
	}
	if title != "" {
		ev.Title = &title
	}
	h.publish(ev)
}

// Append writes one event to stream_log. Its subscribers get it from the next look at the log, which this asks for at once.
func (h *Hub) Append(e Entry) (*Event, error) {
	ev, err := h.Log.Append(e)
	if err == nil {
		select {
		case h.wake <- struct{}{}:
		default:
		}
	}
	return ev, err
}

// AccountChanged appends account.changed on the accounts topic; account is the spec's Account, as accounts.list shows it (it holds no
// credential). A web login calls it each time its state moves.
func (h *Hub) AccountChanged(account any) error {
	_, err := h.Append(Entry{Topic: "accounts", Type: "account.changed", Data: account})
	return err
}

// Run looks at the log for new rows every PollEvery (and when Append asks) until stop closes, and prunes it hourly.
func (h *Hub) Run(stop <-chan struct{}, logf func(string, ...any)) {
	if h.Log == nil {
		return
	}
	tick, prune := time.NewTicker(PollEvery), time.NewTicker(pruneEvery)
	defer tick.Stop()
	defer prune.Stop()
	for {
		select {
		case <-stop:
			return
		case <-tick.C:
		case <-h.wake:
		case <-prune.C:
			if err := h.Log.Prune(); err != nil {
				logf("  stream: prune: %v", err)
			}
			continue
		}
		if err := h.poll(); err != nil {
			logf("  stream: poll: %v", err)
		}
	}
}

// poll hands the rows after the last one handed on to the subscribers, oldest first. With nobody subscribed it only moves its mark: a
// subscriber starts from the newest row, and replays what it missed from the log.
func (h *Hub) poll() error {
	newest, err := h.Log.Newest()
	if err != nil {
		return err
	}
	h.mu.Lock()
	idle, top := len(h.subs) == 0, h.top
	h.mu.Unlock()
	if idle {
		h.setTop(newest)
		return nil
	}
	for top < newest {
		rows, err := h.Log.Since(top, allLogged, "", batch)
		if err != nil || len(rows) == 0 {
			return err
		}
		for _, ev := range rows {
			h.publish(ev)
			top = ev.Cursor
		}
		h.setTop(top)
	}
	return nil
}

func (h *Hub) setTop(n int64) {
	h.mu.Lock()
	h.top = max(h.top, n)
	h.mu.Unlock()
}

var allLogged = func() []string {
	var out []string
	for t := range topics {
		if Daemon(t) {
			out = append(out, t)
		}
	}
	return out
}()

// add registers s. A stream_log subscription first queues what it missed after since (a gap if that is past retention or more than
// ReplayMax), under the lock the poll publishes with, so nothing is missed or sent twice between the replay and the live stream. It
// returns the newest cursor, which is the reply to subscribe.
func (h *Hub) add(s *sub, since *int64) (int64, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	var top int64
	if s.logged && h.Log != nil {
		var err error
		if top, err = h.Log.Newest(); err != nil {
			return 0, err
		}
		if since != nil {
			pruned, err := h.Log.PrunedThrough()
			if err != nil {
				return 0, err
			}
			var missed []*Event
			if *since >= pruned {
				if missed, err = h.Log.Since(*since, s.topicList(), s.agent, ReplayMax+1); err != nil {
					return 0, err
				}
			}
			if *since < pruned || len(missed) > ReplayMax {
				s.preload(nil, gapNotice("retention", top))
			} else {
				s.preload(missed, nil)
			}
		}
		s.last = max(s.last, top)
		h.top = max(h.top, top)
	}
	h.subs[s] = struct{}{}
	return top, nil
}

func (h *Hub) remove(s *sub) {
	h.mu.Lock()
	delete(h.subs, s)
	h.mu.Unlock()
	s.end()
}

// sub is one subscription of a connection: a replay queue and a bounded live queue, written out by one goroutine.
type sub struct {
	send    func([]byte) bool
	hangUp  func()
	topics  map[string]bool
	agent   string // only this agent's events and those of no agent; "" is every agent's
	visible func(*Event) bool
	logged  bool // its topics are stream_log's, so it has cursors

	mu     sync.Mutex
	cond   *sync.Cond
	replay [][]byte // what a resume missed: up to ReplayMax, outside the live bound
	queue  [][]byte // live messages, bounded by MaxQueue
	last   int64    // the newest cursor queued: nothing at or below it is queued twice
	dead   bool
	gap    []byte // the notice that ends this subscription: sent, then the connection is hung up
}

func newSub(c *rpc.Conn, names map[string]bool, agent string, visible func(*Event) bool) *sub {
	s := &sub{send: c.SendRaw, hangUp: c.HangUp, topics: map[string]bool{}, agent: agent, visible: visible}
	for n := range names {
		s.topics[n] = true
		s.logged = Daemon(n)
	}
	s.cond = sync.NewCond(&s.mu)
	return s
}

func (s *sub) topicList() []string {
	var out []string
	for t := range s.topics {
		out = append(out, t)
	}
	return out
}

func (s *sub) wants(ev *Event) bool {
	if !s.topics[ev.Topic] {
		return false
	}
	if s.agent != "" && ev.Agent != nil && *ev.Agent != s.agent {
		return false
	}
	return s.visible == nil || s.visible(ev)
}

// offer queues a live event. A logged one is skipped when its cursor was already queued; cursor 0 is a live-only event.
func (s *sub) offer(ev *Event, msg []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.dead || s.gap != nil {
		return
	}
	if ev.Cursor > 0 {
		if ev.Cursor <= s.last {
			return
		}
		s.last = ev.Cursor
	}
	if !s.wants(ev) {
		return
	}
	if len(s.queue) >= MaxQueue {
		s.queue, s.gap = nil, gapNotice("slow_client", ev.Cursor)
		time.AfterFunc(2*time.Second, s.hangUp) // a writer stuck on a client that stopped reading
	} else {
		s.queue = append(s.queue, msg)
	}
	s.cond.Signal()
}

// preload queues what a resume missed, ahead of everything live. It is not held to MaxQueue: a client reconnecting 5000 events behind is
// normal, and its backlog is not it falling behind.
func (s *sub) preload(events []*Event, notice []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if notice != nil {
		s.replay = append(s.replay, notice)
	}
	for _, ev := range events {
		if ev.Cursor > s.last && s.wants(ev) {
			s.replay = append(s.replay, msgOf(ev))
		}
		s.last = max(s.last, ev.Cursor)
	}
	s.cond.Signal()
}

func (s *sub) end() {
	s.mu.Lock()
	s.dead = true
	s.mu.Unlock()
	s.cond.Broadcast()
}

// run writes the queued notices; it starts after the reply to subscribe is sent, so none comes before it.
func (s *sub) run() {
	for {
		s.mu.Lock()
		for len(s.replay) == 0 && len(s.queue) == 0 && s.gap == nil && !s.dead {
			s.cond.Wait()
		}
		if s.dead {
			s.mu.Unlock()
			return
		}
		if s.gap != nil {
			gap := s.gap
			s.mu.Unlock()
			s.send(gap)
			s.hangUp()
			return
		}
		var msg []byte
		if len(s.replay) > 0 {
			msg, s.replay = s.replay[0], s.replay[1:]
		} else {
			msg, s.queue = s.queue[0], s.queue[1:]
		}
		s.mu.Unlock()
		if !s.send(msg) {
			s.end()
			return
		}
	}
}

func gapNotice(reason string, cursor int64) []byte {
	b, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "method": "gap", "params": map[string]any{"reason": reason, "cursor": cursor}})
	return b
}

// Register adds subscribe and unsubscribe to a server. A subscribe names devices alone, transcript alone, or stream_log's topics
// (else bad_request naming topics). devices is for a founder and transcript for a member; an agent session is sent only what its role may read.
func Register(srv *rpc.Server, h *Hub) {
	var mu sync.Mutex
	byConn := map[*rpc.Conn][]*sub{}
	drop := func(c *rpc.Conn) {
		mu.Lock()
		subs := byConn[c]
		delete(byConn, c)
		mu.Unlock()
		for _, s := range subs {
			h.remove(s)
		}
	}
	srv.Register("subscribe", rpc.Method{Role: "agent", Handler: func(c *rpc.Conn, p map[string]any) (any, error) {
		names, err := check(p)
		if err != nil {
			return nil, err
		}
		since, err := sinceOf(p)
		if err != nil {
			return nil, err
		}
		for n := range names {
			if err := Allowed(c, n); err != nil {
				return nil, err
			}
		}
		switch {
		case names[devicesTopic]:
			if !c.Caller.Allows("founder") {
				return nil, rpc.Refused("the devices topic is for a founder; this caller is "+c.Caller.Role, "role", "")
			}
		case names[transcriptTopic]:
			if !c.Caller.Allows("member") {
				return nil, rpc.Refused("the transcript topic is for a member; this caller is "+c.Caller.Role, "role", "")
			}
		}
		agent := ""
		if v, has := p["agent"]; has && v != nil {
			name, ok := v.(string)
			if !ok {
				return nil, rpc.BadRequestError("agent is a name", "agent", rpc.InvalidParams)
			}
			agent = name
		}
		live := names[devicesTopic] || names[transcriptTopic]
		var visible func(*Event) bool
		if c.Caller.Role == "agent" {
			name, _ := c.Caller.Info["name"].(string)
			visible = newAgentView(name, h.Reach).may
		}
		mu.Lock()
		defer mu.Unlock()
		if live { // a second subscribe to a live topic replaces the first's filter
			for _, old := range byConn[c] {
				if old.topics[devicesTopic] == names[devicesTopic] && old.topics[transcriptTopic] == names[transcriptTopic] {
					old.mu.Lock()
					old.agent = agent
					old.mu.Unlock()
					return map[string]any{"cursor": 0}, nil
				}
			}
		}
		s := newSub(c, names, agent, visible)
		top, err := h.add(s, since)
		if err != nil {
			return nil, err
		}
		if len(byConn[c]) == 0 {
			c.OnClose(func() { drop(c) })
		}
		byConn[c] = append(byConn[c], s)
		c.AfterReply(func() { go s.run() })
		return map[string]any{"cursor": top}, nil
	}})
	srv.Register("unsubscribe", rpc.Method{Role: "agent", Handler: func(c *rpc.Conn, p map[string]any) (any, error) {
		names, err := check(p)
		if err != nil {
			return nil, err
		}
		mu.Lock()
		defer mu.Unlock()
		var keep []*sub
		for _, s := range byConn[c] {
			s.mu.Lock()
			for n := range names {
				delete(s.topics, n)
			}
			empty := len(s.topics) == 0
			s.mu.Unlock()
			if empty {
				h.remove(s)
			} else {
				keep = append(keep, s)
			}
		}
		byConn[c] = keep
		return map[string]any{}, nil
	}})
}

// sinceOf is the since param: nil when absent, else a non-negative cursor.
func sinceOf(p map[string]any) (*int64, error) {
	v, has := p["since"]
	if !has || v == nil {
		return nil, nil
	}
	n, ok := rpc.AsInt(v)
	if !ok || n < 0 {
		return nil, rpc.BadRequestError("since is a cursor, a non-negative integer", "since", rpc.InvalidParams)
	}
	return &n, nil
}

// agentView is which events one agent session may be sent: a topic delivers to a role only what that role's read would return.
type agentView struct {
	name  string
	reach func(string) (map[string]bool, error)
	set   map[string]bool
	at    time.Time
}

const reachTTL = 10 * time.Second

func newAgentView(name string, reach func(string) (map[string]bool, error)) *agentView {
	return &agentView{name: name, reach: reach}
}

// reachSet is the agents this session reaches, itself included, read again after reachTTL. A read that fails is not kept: the session is
// sent what it may certainly read, and the next event asks again.
func (v *agentView) reachSet() map[string]bool {
	if v.set != nil && time.Since(v.at) < reachTTL {
		return v.set
	}
	set := map[string]bool{}
	if v.reach != nil {
		if got, err := v.reach(v.name); err == nil {
			set = got
			v.set, v.at = set, time.Now()
		}
	}
	set[v.name] = true
	return set
}

func (v *agentView) may(ev *Event) bool {
	who := ""
	if ev.Agent != nil {
		who = *ev.Agent
	}
	var reach map[string]bool
	if ev.Topic == "tasks" || ev.Topic == "notifications" {
		reach = v.reachSet()
	}
	return AgentMay(v.name, ev.Topic, ev.Type, who, reach)
}

// topicNames is the topics param as a set; false when it is not a non-empty list of strings.
func topicNames(p map[string]any) (map[string]bool, bool) {
	list, ok := p["topics"].([]any)
	if !ok || len(list) == 0 {
		return nil, false
	}
	out := map[string]bool{}
	for _, v := range list {
		name, ok := v.(string)
		if !ok {
			return nil, false
		}
		out[name] = true
	}
	return out, true
}

// check judges the topics param: known topics only, and devices by itself.
func check(p map[string]any) (map[string]bool, error) {
	names, ok := topicNames(p)
	if !ok {
		return nil, rpc.BadRequestError("topics is a list of topic names", "topics", rpc.InvalidParams)
	}
	for n := range names {
		if !topics[n] {
			return nil, rpc.BadRequestError("no such topic: "+n, "topics", rpc.InvalidParams)
		}
	}
	if names[devicesTopic] && len(names) > 1 {
		return nil, rpc.BadRequestError("devices is subscribed on its own: it is this gateway's, every other topic is the daemon's", "topics", rpc.InvalidParams)
	}
	if names[transcriptTopic] && len(names) > 1 {
		return nil, rpc.BadRequestError("transcript is subscribed on its own: this daemon serves it live, the other topics are the Python daemon's", "topics", rpc.InvalidParams)
	}
	return names, nil
}

// memberTopics are the topics an agent may not follow: the reads behind them (chat.*, operation.get) are a member's.
var memberTopics = map[string]bool{"chat": true, "operations": true}

// Allowed refuses a caller below a member any of the named topics that are a member's.
func Allowed(c *rpc.Conn, names ...string) error {
	for _, n := range names {
		if memberTopics[n] && !c.Caller.Allows("member") {
			return rpc.Refused("the "+n+" topic is for a member or above; this caller is "+c.Caller.Role, "role", "")
		}
	}
	return nil
}

// AgentMay is whether an event may be sent to the agent `name`: a topic delivers to a role only what that role's read would return. An agent's
// inbox.list is its own, its tasks are its own and its reports' (reach), and prompt.list is a member's. Whatever no rule names is not sent.
// topic, typ and who (the event's agent, "" for none) are the event's.
func AgentMay(name, topic, typ, who string, reach map[string]bool) bool {
	switch topic {
	case "accounts", "agents", "hr", "status":
		return true
	case "inbox":
		return who == name
	case "tasks":
		return reach[who]
	case "notifications":
		return (typ == "account_out" || typ == "stall" || typ == "task_done") && reach[who]
	}
	return false
}

// Daemon reports whether name is a topic of stream_log (every topic but devices and transcript, which are live from memory).
func Daemon(name string) bool { return topics[name] && name != devicesTopic && name != transcriptTopic }
