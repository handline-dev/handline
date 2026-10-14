package turns

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
)

// interrupted are the texts of the user entry Claude Code writes when Esc ends a turn: the second when a tool was running or asking.
var interrupted = map[string]bool{"[Request interrupted by user]": true, "[Request interrupted by user for tool use]": true}

func interruptEntry(line []byte) bool {
	var e struct {
		Type    string `json:"type"`
		Message struct {
			Content json.RawMessage `json:"content"`
		} `json:"message"`
	}
	if json.Unmarshal(line, &e) != nil || e.Type != "user" {
		return false
	}
	var text string
	if json.Unmarshal(e.Message.Content, &text) == nil {
		return interrupted[text]
	}
	var parts []struct{ Type, Text string }
	json.Unmarshal(e.Message.Content, &parts)
	for _, p := range parts {
		if p.Type == "text" && interrupted[p.Text] {
			return true
		}
	}
	return false
}

// tail hands on each whole line appended to a file since it was opened.
type tail struct {
	f       *os.File
	partial []byte
}

func newTail(path string) (*tail, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	// From the end: what is already there belongs to turns before this one. An Esc in the moment between the prompt's hook and
	// this open is not seen, and the turn then stays open until the next prompt.
	if _, err := f.Seek(0, io.SeekEnd); err != nil {
		f.Close()
		return nil, err
	}
	return &tail{f: f}, nil
}

func (t *tail) read(line func([]byte)) {
	buf := make([]byte, 64<<10)
	for {
		n, err := t.f.Read(buf)
		t.partial = append(t.partial, buf[:n]...)
		for {
			i := bytes.IndexByte(t.partial, '\n')
			if i < 0 {
				break
			}
			line(t.partial[:i])
			t.partial = t.partial[i+1:]
		}
		if err != nil || n == 0 {
			return
		}
	}
}

// watchLines calls line for each line appended to path, woken by the file system, until stop is called.
func watchLines(path string, line func([]byte)) (stop func(), err error) {
	t, err := newTail(path)
	if err != nil {
		return nil, err
	}
	stop, err = watchFile(path, func() { t.read(line) }, func() { t.f.Close() })
	if err != nil {
		t.f.Close()
	}
	return stop, err
}
