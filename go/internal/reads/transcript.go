package reads

import (
	"fmt"

	"github.com/handline/handline/go/internal/rpc"
)

const transcriptPage = 100

// transcript.events: a page of an agent's transcript events by seq (contract: Transcript stream). Structured sessions wrote the stream;
// no agent runs one now, so after its checks every agent is refused as a terminal agent always was.
func (e *Env) transcriptEvents(c *rpc.Conn, params map[string]any) (any, error) {
	since, err := seqParam(params, "since_seq", 0)
	if err != nil {
		return nil, err
	}
	before, err := seqParam(params, "before_seq", 1)
	if err != nil {
		return nil, err
	}
	if since != nil && before != nil {
		return nil, badParam("since_seq and before_seq are one or the other", "since_seq")
	}
	if _, err := limitOr(params, 500, transcriptPage); err != nil {
		return nil, err
	}
	agent, err := e.chatAgent(params, true)
	if err != nil {
		return nil, err
	}
	return nil, rpc.Refused(agent+" does not run as a structured session: it has no transcript stream, watch its terminal", "unsupported", "")
}

// seqParam is an optional seq at or above min.
func seqParam(params map[string]any, key string, min int64) (*int64, error) {
	n, err := rangedInt(params, key, 0, 0)
	if err != nil || n == nil {
		return n, err
	}
	if *n < min {
		return nil, badParam(fmt.Sprintf("%s is an integer from %d", key, min), key)
	}
	return n, nil
}

// PromptOf is the spec's Prompt of the prompt row id of agent, and when it was first seen: what a permission event carries.
func (e *Env) PromptOf(agent string, id int64) (map[string]any, int64, bool) {
	r, err := queryOne(e.DB, "SELECT * FROM prompt_blocks WHERE id = ? AND agent = ?", id, agent)
	if err != nil || r == nil {
		return nil, 0, false
	}
	return promptOf(agent, r), r.i("first_seen"), true
}
