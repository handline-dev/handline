package compose

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"os"
)

// tailBytes is how much of a transcript's end is read for its last reply: a reply with a long thinking block or a large tool result is big.
const tailBytes = 8 << 20

// lastContext is what the newest reply in a Claude transcript says the context holds: its input, cache-read and cache-write tokens, and the
// model that gave it. ok false when the file has no reply in its tail. A structured session writes no status file; the transcript the CLI
// keeps is the one place its context shows.
func lastContext(path string) (used int64, model string, ok bool) {
	f, err := os.Open(path)
	if err != nil {
		return 0, "", false
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return 0, "", false
	}
	off := max(0, st.Size()-tailBytes)
	if _, err := f.Seek(off, io.SeekStart); err != nil {
		return 0, "", false
	}
	r := bufio.NewReader(f)
	if off > 0 {
		_, _ = r.ReadBytes('\n') // the first line of a cut tail is a fragment
	}
	for {
		raw, err := r.ReadBytes('\n')
		if line := bytes.TrimSpace(raw); len(line) > 0 {
			var e struct {
				Type    string `json:"type"`
				Side    bool   `json:"isSidechain"`
				Message struct {
					Model string         `json:"model"`
					Raw   map[string]any `json:"usage"`
				} `json:"message"`
			}
			if json.Unmarshal(line, &e) == nil && e.Type == "assistant" && !e.Side && e.Message.Raw != nil {
				var sum int64
				for _, k := range []string{"input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"} {
					if v, isNum := e.Message.Raw[k].(float64); isNum {
						sum += int64(v)
					}
				}
				used, model, ok = sum, e.Message.Model, true
			}
		}
		if err != nil {
			break
		}
	}
	return used, model, ok
}
