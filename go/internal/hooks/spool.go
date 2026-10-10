package hooks

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

// The hook spool: where a hook leaves an event when the daemon is down (handline/daemon/spool.py). The daemon reads
// the file and removes it; each line is applied once, by its request id.
const (
	spoolName    = "hook-spool.jsonl"
	rejectedName = "hook-spool-rejected.jsonl"
)

func spoolPath(home string) string { return filepath.Join(home, spoolName) }

func ingesting(home string) string { return filepath.Join(home, "hook-spool.ingesting") }

// spoolPending is whether a file left by an ingest that did not finish waits: events must keep the ids it may replay.
func spoolPending(home string) bool {
	_, err := os.Stat(ingesting(home))
	return err == nil
}

type spoolLine struct {
	agent, kind, requestID string
	data                   map[string]any
	at                     int64
}

// parse is a line as the Python ingest reads it: the four keys and a request id, whatever its type, as text.
func parse(raw string) (spoolLine, error) {
	d := json.NewDecoder(strings.NewReader(raw))
	d.UseNumber()
	var m map[string]any
	if err := d.Decode(&m); err != nil {
		return spoolLine{}, fmt.Errorf("not JSON: %w", err)
	}
	if d.More() {
		return spoolLine{}, fmt.Errorf("extra data after the first JSON value")
	}
	var l spoolLine
	var ok bool
	if l.agent, ok = m["agent"].(string); !ok || l.agent == "" {
		return l, fmt.Errorf("agent is a name")
	}
	if l.kind, ok = m["kind"].(string); !ok || l.kind == "" {
		return l, fmt.Errorf("kind is a hook event name")
	}
	if l.data, ok = m["data"].(map[string]any); !ok {
		return l, fmt.Errorf("data is the hook's own object")
	}
	switch id := m["request_id"].(type) {
	case nil:
		return l, fmt.Errorf("no request_id")
	case string:
		l.requestID = id
	default:
		l.requestID = fmt.Sprint(id)
	}
	switch at := m["at"].(type) {
	case json.Number:
		if n, err := at.Int64(); err == nil {
			l.at = n
		} else if f, err := at.Float64(); err == nil {
			l.at = int64(f)
		} else {
			return l, fmt.Errorf("at is not a number")
		}
	default:
		return l, fmt.Errorf("at is when the hook ran, in epoch seconds")
	}
	if !safeName(l.agent) {
		return l, fmt.Errorf("agent is not a name this daemon keeps files for")
	}
	return l, nil
}

func safeName(s string) bool {
	return s != "" && s != "." && s != ".." && !strings.ContainsAny(s, "/\\\x00")
}

// Ingest applies every spooled line, then removes the file; the number applied. The file is renamed first, so a
// hook writing meanwhile starts a new one. A line that cannot be parsed is set aside; on a failure of the database
// the lines not yet applied stay in the file for the next try, and the ones already applied are safe to see again.
func (h *Recorder) Ingest() (int, error) {
	work := ingesting(h.Home)
	if _, err := os.Stat(spoolPath(h.Home)); err == nil {
		if _, err := os.Stat(work); err != nil {
			if err := os.Rename(spoolPath(h.Home), work); err != nil {
				return 0, err
			}
		}
	}
	b, err := os.ReadFile(work)
	if os.IsNotExist(err) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	text := strings.ReplaceAll(strings.ToValidUTF8(string(b), "�"), "\r\n", "\n")
	lines := strings.Split(text, "\n")
	if lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	n, rejected := 0, 0
	for i, raw := range lines {
		l, perr := parse(raw)
		if perr != nil {
			if !h.reject(raw, "ValueError", perr.Error()) {
				return n, keep(work, lines[i:])
			}
			rejected++
			continue
		}
		if err := Apply(h.DB, h.Home, l.agent, l.kind, l.data, l.requestID, l.at); err != nil {
			h.Log("  spool: stopped at a line the database could not take (%v); %d line(s) left for the next try", err, len(lines)-i)
			if kerr := keep(work, lines[i:]); kerr != nil {
				return n, kerr
			}
			return n, err
		}
		n++
	}
	if rejected > 0 {
		h.Log("  spool: %d line(s) set aside in %s", rejected, filepath.Join(h.Home, rejectedName))
	}
	return n, os.Remove(work)
}

// reject sets a line that cannot be applied aside, whole, with what went wrong and when. False when even that cannot
// be written, and the line then stays in the spool.
func (h *Recorder) reject(raw, kind, message string) bool {
	if len(message) > 300 {
		message = string([]rune(message)[:300])
	}
	rec, _ := json.Marshal(map[string]any{"rejected_at": time.Now().Unix(), "error": kind, "message": message, "line": raw})
	f, err := os.OpenFile(filepath.Join(h.Home, rejectedName), os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o600)
	if err == nil {
		defer f.Close()
		if err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err == nil {
			_, err = f.Write(append(rec, '\n'))
		}
	}
	if err != nil {
		h.Log("  spool: a line that cannot be applied could not be set aside either (%v); it stays in the spool", err)
		return false
	}
	h.Log("  spool: set aside a line (%s: %s)", kind, message)
	return true
}

// keep leaves only rest in the file being ingested: written whole beside it and swapped in.
func keep(work string, rest []string) error {
	tmp := strings.TrimSuffix(work, ".ingesting") + ".keeping"
	if err := os.WriteFile(tmp, []byte(strings.Join(rest, "\n")+"\n"), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, work)
}

// AppendSpool leaves one event in the spool for the daemon (spool.append): one line, written whole under an exclusive
// lock, so lines from hooks running at once never mix.
func AppendSpool(home, agent, kind string, data map[string]any, requestID string, at int64) error {
	line := fmt.Sprintf(`{"agent": %s, "kind": %s, "at": %d, "data": %s, "request_id": %s}`+"\n",
		rpc.PyJSON(agent, false, true), rpc.PyJSON(kind, false, true), at, rpc.PyJSON(data, false, true), rpc.PyJSON(requestID, false, true))
	f, err := os.OpenFile(spoolPath(home), os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err != nil {
		return err
	}
	_, err = f.WriteString(line)
	return err
}
