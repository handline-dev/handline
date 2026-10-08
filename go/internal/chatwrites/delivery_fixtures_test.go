package chatwrites

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/supervisor"
)

// The fixtures of contract/fixtures/delivery.json, which need structured agents the daemon binary cannot host yet, played here against the
// chat writes and reads on the same server: bob is idle and echoes during a fixture's sleep, carol has a turn running, dave lost an operation, alice is a terminal agent.

func matchFixture(want, got any, saved map[string]any, at string) error {
	if w, ok := want.(map[string]any); ok {
		special := false
		for k := range w {
			special = special || strings.HasPrefix(k, "$")
		}
		if !special {
			g, ok := got.(map[string]any)
			if !ok {
				return fmt.Errorf("%s: wanted an object, got %v", at, got)
			}
			for k, v := range w {
				if m, ok := v.(map[string]any); ok && m["$absent"] == true {
					if _, has := g[k]; has {
						return fmt.Errorf("%s.%s: wanted absent", at, k)
					}
					continue
				}
				if _, has := g[k]; !has {
					return fmt.Errorf("%s: no %s in %v", at, k, g)
				}
				if err := matchFixture(v, g[k], saved, at+"."+k); err != nil {
					return err
				}
			}
			return nil
		}
		for op, arg := range w {
			switch op {
			case "$type":
				if _, isNum := got.(float64); arg == "integer" && !isNum {
					return fmt.Errorf("%s: wanted an integer, got %v", at, got)
				}
			case "$oneOf":
				ok := false
				for _, alt := range arg.([]any) {
					ok = ok || matchFixture(alt, got, saved, at) == nil
				}
				if !ok {
					return fmt.Errorf("%s: wanted one of %v, got %v", at, arg, got)
				}
			case "$contains":
				list, _ := got.([]any)
				ok := false
				for _, item := range list {
					ok = ok || matchFixture(arg, item, saved, at) == nil
				}
				if !ok {
					return fmt.Errorf("%s: no element matches %v in %v", at, arg, got)
				}
			case "$regex":
				if s, ok := got.(string); !ok || !regexp.MustCompile(arg.(string)).MatchString(s) {
					return fmt.Errorf("%s: wanted /%v/, got %v", at, arg, got)
				}
			case "$save":
				saved[arg.(string)] = got
			default:
				return fmt.Errorf("%s: matcher %s", at, op)
			}
		}
		return nil
	}
	if fmt.Sprint(want) != fmt.Sprint(got) {
		return fmt.Errorf("%s: wanted %v, got %v", at, want, got)
	}
	return nil
}

func fill(v any, vars map[string]any) any {
	switch x := v.(type) {
	case string:
		if strings.HasPrefix(x, "{{") && strings.HasSuffix(x, "}}") {
			if val, ok := vars[x[2:len(x)-2]]; ok {
				return val
			}
		}
		return x
	case map[string]any:
		out := map[string]any{}
		for k, e := range x {
			out[k] = fill(e, vars)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = fill(e, vars)
		}
		return out
	}
	return v
}

func TestTheDeliveryFixturesPassAgainstTheChatWritesAndReads(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "contract", "fixtures", "delivery.json"))
	if err != nil {
		t.Fatal(err)
	}
	var file struct {
		Fixtures []struct {
			Name  string
			Steps []map[string]any
		}
	}
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatal(err)
	}
	for _, fx := range file.Fixtures {
		t.Run(fx.Name, func(t *testing.T) {
			r := newRig(t)
			team, _ := os.ReadFile(filepath.Join(r.home, "team.yaml"))
			os.WriteFile(filepath.Join(r.home, "team.yaml"), []byte(strings.Replace(string(team), "agents:\n", "agents:\n  carol: {}\n  dave: {}\n", 1)), 0o644)
			for _, a := range []string{"carol", "dave"} {
				os.MkdirAll(filepath.Join(r.home, "agents", a), 0o755)
				os.WriteFile(filepath.Join(r.home, "agents", a, "agent.yaml"), []byte("vendor: claude\n"), 0o644)
			}
			f := &fakeSessions{r: r, busyFor: map[string]bool{"carol": true}}
			r.env.Agents = f
			srv := r.server("")
			reads.Register(srv, reads.Env{DB: r.db, Home: r.home, Started: time.Now()})
			// dave's operation was written, and the session ended before it was echoed
			r.one(srv, "chat.send", `{"agent":"dave","body":"in flight when it died","request_id":"crashed-op"}`)
			f.setState("dave", supervisor.RequestOp(1), structured.Unknown)
			vars := map[string]any{"founder_key": "founder", "transcript_agent": "bob", "working_agent": "carol", "crashed_agent": "dave", "terminal_agent": "alice"}
			saved := map[string]any{}
			cl, sv := net.Pipe()
			done := make(chan struct{})
			go func() { srv.Serve(rpc.NewLineTransport(sv), 0); sv.Close(); close(done) }()
			defer func() { cl.Close(); <-done }()
			rd := bufio.NewReader(cl)
			cl.SetDeadline(time.Now().Add(20 * time.Second))
			for n, step := range fx.Steps {
				if s, ok := step["sleep_s"].(float64); ok {
					f.confirmAll() // the CLI echoes while the client waits
					time.Sleep(time.Duration(s * float64(time.Second)))
				}
				line, _ := json.Marshal(fill(step["send"], mergeVars(vars, saved)))
				cl.Write(append(line, '\n'))
				b, err := rd.ReadBytes('\n')
				if err != nil {
					t.Fatal(err)
				}
				var reply map[string]any
				json.Unmarshal(b, &reply)
				var want any
				got := reply["result"]
				if want = step["result"]; want == nil {
					want, got = step["error"], reply["error"]
				}
				if err := matchFixture(fill(want, mergeVars(vars, saved)), got, saved, "$"); err != nil {
					t.Fatalf("step %d: %v\nreply %s", n+1, err, b)
				}
			}
		})
	}
}

func mergeVars(a, b map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range a {
		out[k] = v
	}
	for k, v := range b {
		out[k] = v
	}
	return out
}
