package chatwrites

import (
	"bufio"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/fixtureplay"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/structured"
	"github.com/handline/handline/go/internal/supervisor"
)

// The fixtures of contract/fixtures/delivery.json, which need structured agents the daemon binary cannot host yet, played here against the
// chat writes and reads on the same server: bob is idle and echoes during a fixture's sleep, carol has a turn running, dave lost an operation, alice is a terminal agent.

func TestTheDeliveryFixturesPassAgainstTheChatWritesAndReads(t *testing.T) {
	fixtures, err := fixtureplay.Load(filepath.Join("..", "..", "..", "contract", "fixtures", "delivery.json"))
	if err != nil {
		t.Fatal(err)
	}
	for _, fx := range fixtures {
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
				line, _ := json.Marshal(fixtureplay.Fill(step["send"], mergeVars(vars, saved)))
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
				if err := fixtureplay.Match(fixtureplay.Fill(want, mergeVars(vars, saved)), got, saved, "$"); err != nil {
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
