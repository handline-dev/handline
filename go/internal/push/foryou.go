package push

import (
	"context"
	"fmt"

	"github.com/handline/handline/go/internal/stream"
)

// The first word of a For you note's title, by what the item asks of the founder.
var asksWord = map[string]string{"allow": "Allow", "decide": "Decide", "review": "Review", "do": "Do by hand"}

// ForYouNote is the note of a for_you event: an item that joins the count is shown, with its id as the collapse id and the data a tap
// opens; one that leaves it is a silent badge update. A deferred item left with for_you.closed and is never opened, so it sends nothing
// shown; one back from Later opens again. Any other event has no note.
func ForYouNote(e stream.Entry) (Note, bool) {
	data, _ := e.Data.(map[string]any)
	count, _ := data["count"].(int)
	switch e.Type {
	case "for_you.opened":
		it, _ := data["item"].(map[string]any)
		kind, id := fmt.Sprint(it["kind"]), fmt.Sprint(it["id"])
		title := asksWord[fmt.Sprint(it["asks"])]
		if agent, _ := it["agent"].(string); agent != "" {
			title += " · " + agent
		}
		return Note{Title: title, Body: e.Title, Data: map[string]string{"kind": kind, "id": id}, Collapse: kind + ":" + id, Badge: count}, true
	case "for_you.closed":
		return Note{Badge: count, Silent: true}, true
	}
	return Note{}, false
}

// Emit appends one event to the stream (stream.Hub.Append).
type Emit = func(stream.Entry) (*stream.Event, error)

// Tee is emit that also puts each for_you event it appended on notes for Watch. It never waits: with notes full the event is logged and
// gets no note, so a slow push service does not hold the caller.
func (s *Sender) Tee(emit Emit, notes chan<- stream.Entry) Emit {
	return func(e stream.Entry) (*stream.Event, error) {
		ev, err := emit(e)
		if err == nil && e.Topic == "for_you" {
			select {
			case notes <- e:
			default:
				s.log("push: the queue is full, no note for %s", e.Type)
			}
		}
		return ev, err
	}
}

// Watch sends the note of each event from events, one at a time and in order, until stop or events closes.
func (s *Sender) Watch(stop <-chan struct{}, events <-chan stream.Entry) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() {
		select {
		case <-stop:
			cancel()
		case <-ctx.Done():
		}
	}()
	for {
		select {
		case <-stop:
			return
		case e, open := <-events:
			if !open {
				return
			}
			if n, ok := ForYouNote(e); ok {
				if err := s.Notify(ctx, n); err != nil {
					s.log("push: %v", err)
				}
			}
		}
	}
}

// Platforms is the platforms s sends to, none when push is off.
func (s *Sender) Platforms() []string {
	out := []string{}
	for _, p := range []string{"apns", "fcm"} {
		if s.On(p) {
			out = append(out, p)
		}
	}
	return out
}
