// Package stream is subscribe and unsubscribe for the one topic this daemon owns: devices. Pairing is the Go
// daemon's, so it fans device.paired and device.revoked out itself, in memory, to the founder connections
// subscribed to them. Every other topic is the Python daemon's, whose stream_log this daemon never writes: behind a
// gateway a subscribe for them is relayed; without one nothing here emits them.
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
	// MaxQueue is how many events may wait for one client before it is dropped with a gap.
	MaxQueue = 1000
)

var topics = map[string]bool{"status": true, "accounts": true, "agents": true, "inbox": true, "tasks": true,
	"notifications": true, "operations": true, "chat": true, devicesTopic: true, "hr": true, transcriptTopic: true}

// Hub fans events out to the connections subscribed to the devices topic.
type Hub struct {
	mu   sync.Mutex
	subs map[*sub]struct{}
}

func NewHub() *Hub { return &Hub{subs: map[*sub]struct{}{}} }

// EmitTranscript sends a transcript event (a committed row, as transcript.events shows it) to the subscribers of the transcript topic
// that want its agent. Events are live only and carry cursor 0: a client that missed any reads transcript.events from its last seq.
func (h *Hub) EmitTranscript(agent string, ev map[string]any) {
	msg, err := json.Marshal(map[string]any{"jsonrpc": "2.0", "method": "event", "params": map[string]any{
		"cursor": 0, "topic": transcriptTopic, "type": "transcript.event", "at": ev["at"],
		"actor": nil, "agent": agent, "title": nil, "target": nil, "data": ev}})
	if err != nil {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	for s := range h.subs {
		if s.topic == transcriptTopic && (s.agent == "" || s.agent == agent) {
			s.offer(msg)
		}
	}
}

// Emit sends a devices event to every subscriber. Events are live only and carry cursor 0: the stream's cursors
// are the Python daemon's, and a client resumes the devices topic from devices.list.
func (h *Hub) Emit(typ string, data, actor map[string]any, title string) {
	var t, a any
	if title != "" {
		t = title
	}
	if actor != nil {
		a = actor
	}
	msg, err := json.Marshal(map[string]any{"jsonrpc": "2.0", "method": "event", "params": map[string]any{
		"cursor": 0, "topic": devicesTopic, "type": typ, "at": time.Now().UTC().Format("2006-01-02T15:04:05Z"),
		"actor": a, "agent": nil, "title": t, "target": nil, "data": data}})
	if err != nil {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	for s := range h.subs {
		if s.topic == devicesTopic {
			s.offer(msg)
		}
	}
}

// sub is one connection's subscription: a bounded queue, written out by one goroutine.
type sub struct {
	send   func([]byte) bool
	hangUp func()
	topic  string
	agent  string // transcript: only this agent's events; "" is every agent's

	mu    sync.Mutex
	cond  *sync.Cond
	queue [][]byte
	dead  bool
	slow  bool // the queue overflowed: send a gap, then hang up
}

func newSub(c *rpc.Conn, topic, agent string) *sub {
	s := &sub{send: c.SendRaw, hangUp: c.HangUp, topic: topic, agent: agent}
	s.cond = sync.NewCond(&s.mu)
	return s
}

func (s *sub) offer(msg []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.dead || s.slow {
		return
	}
	if len(s.queue) >= MaxQueue {
		s.queue, s.slow = nil, true
		time.AfterFunc(2*time.Second, s.hangUp) // a writer stuck on a client that stopped reading
	} else {
		s.queue = append(s.queue, msg)
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
		for len(s.queue) == 0 && !s.slow && !s.dead {
			s.cond.Wait()
		}
		if s.dead || s.slow {
			slow := s.slow && !s.dead
			s.mu.Unlock()
			if slow {
				s.send(gapNotice())
				s.hangUp()
			}
			return
		}
		msg := s.queue[0]
		s.queue = s.queue[1:]
		s.mu.Unlock()
		if !s.send(msg) {
			s.end()
			return
		}
	}
}

func gapNotice() []byte {
	b, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "method": "gap", "params": map[string]any{"reason": "slow_client", "cursor": 0}})
	return b
}

// Register adds subscribe and unsubscribe to a server. A subscribe naming devices names only devices (else
// bad_request naming topics: one subscription cannot be both this gateway's and the daemon's) and is for the
// founder. A subscribe for other topics on a daemon with no relay (the gateway's own socket, or no gateway) is
// answered with cursor 0 and then sends nothing, since nothing here emits those topics.
func Register(srv *rpc.Server, h *Hub) {
	var mu sync.Mutex
	byConn := map[*rpc.Conn]*sub{}
	drop := func(c *rpc.Conn) {
		mu.Lock()
		s := byConn[c]
		delete(byConn, c)
		mu.Unlock()
		if s != nil {
			h.mu.Lock()
			delete(h.subs, s)
			h.mu.Unlock()
			s.end()
		}
	}
	// What is not plainly devices is the daemon behind a gateway to judge, malformed params included.
	relays := func(p map[string]any) bool {
		names, ok := topicNames(p)
		return !ok || !(names[devicesTopic] || names[transcriptTopic])
	}
	srv.Register("subscribe", rpc.Method{Role: "agent", Relays: relays, Handler: func(c *rpc.Conn, p map[string]any) (any, error) {
		names, err := check(p)
		if err != nil {
			return nil, err
		}
		topic := devicesTopic
		switch {
		case names[devicesTopic]:
			if !c.Caller.Allows("founder") {
				return nil, rpc.Refused("the devices topic is for a founder; this caller is "+c.Caller.Role, "role", "")
			}
		case names[transcriptTopic]:
			topic = transcriptTopic
			if !c.Caller.Allows("member") {
				return nil, rpc.Refused("the transcript topic is for a member; this caller is "+c.Caller.Role, "role", "")
			}
		default:
			return map[string]any{"cursor": 0}, nil
		}
		agent, _ := p["agent"].(string)
		mu.Lock()
		defer mu.Unlock()
		if old := byConn[c]; old != nil { // a second subscribe replaces the first's filter
			h.mu.Lock()
			old.topic, old.agent = topic, agent
			h.mu.Unlock()
		} else {
			s := newSub(c, topic, agent)
			byConn[c] = s
			h.mu.Lock()
			h.subs[s] = struct{}{}
			h.mu.Unlock()
			c.OnClose(func() { drop(c) })
			c.AfterReply(func() { go s.run() })
		}
		return map[string]any{"cursor": 0}, nil
	}})
	srv.Register("unsubscribe", rpc.Method{Role: "agent", Relays: relays, Handler: func(c *rpc.Conn, p map[string]any) (any, error) {
		names, err := check(p)
		if err != nil {
			return nil, err
		}
		if names[devicesTopic] || names[transcriptTopic] {
			drop(c)
		}
		return map[string]any{}, nil
	}})
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

// Daemon reports whether name is a topic the Python daemon's stream_log holds (every topic but devices, which is this
// gateway's own).
func Daemon(name string) bool { return topics[name] && name != devicesTopic && name != transcriptTopic }
