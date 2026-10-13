package taskwrites_test

import (
	"bufio"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/fixtureplay"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/taskwrites"
)

// The fixtures of contract/fixtures/task_report.json, played here against the task writes and reads: the daemon binary's conformance run
// has no agent connection, and its accept and reject fixtures need one. The agent is w; a founder connection is the founder.
func TestTheTaskReportFixturesPassAgainstTheTaskWritesAndReads(t *testing.T) {
	fixtures, err := fixtureplay.Load(filepath.Join("..", "..", "..", "contract", "fixtures", "task_report.json"))
	if err != nil {
		t.Fatal(err)
	}
	for _, fx := range fixtures {
		t.Run(fx.Name, func(t *testing.T) {
			sdb, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
			if err != nil {
				t.Fatal(err)
			}
			defer sdb.Close()
			home := t.TempDir()
			os.MkdirAll(filepath.Join(home, "agents", "w"), 0o755)
			os.WriteFile(filepath.Join(home, "agents", "w", "agent.yaml"), nil, 0o644)
			tell := taskwrites.NewFakeTeller()
			roles := map[string]string{"": fx.As}
			for name, role := range fx.With {
				roles[name] = role
			}
			conns := map[string]*conn{}
			for name, role := range roles {
				agent := "w"
				if role == "founder" {
					agent = ""
				}
				s := rpc.NewServer(taskwrites.AuthAs(agent), "x", store.LatestVersion(), "local", func(string, ...any) {})
				s.DB = sdb
				taskwrites.Register(s, home, tell)
				reads.Register(s, reads.Env{DB: sdb, Home: home, Started: time.Now()})
				conns[name] = open(t, s)
			}
			vars := map[string]any{"founder_key": "founder"}
			for n, step := range fx.Steps {
				on, _ := step["on"].(string)
				reply := conns[on].call(t, fixtureplay.Fill(step["send"], vars))
				want, got := step["result"], reply["result"]
				if want == nil {
					want, got = step["error"], reply["error"]
				}
				if err := fixtureplay.Match(fixtureplay.Fill(want, vars), got, vars, "$"); err != nil {
					t.Fatalf("step %d: %v\nreply %v", n+1, err, reply)
				}
			}
		})
	}
}

// conn is one connection to a server, its replies read in turn.
type conn struct {
	c net.Conn
	r *bufio.Reader
}

func open(t *testing.T, s *rpc.Server) *conn {
	cl, sv := net.Pipe()
	done := make(chan struct{})
	go func() { s.Serve(rpc.NewLineTransport(sv), 0); sv.Close(); close(done) }()
	t.Cleanup(func() { cl.Close(); <-done })
	cl.SetDeadline(time.Now().Add(20 * time.Second))
	return &conn{cl, bufio.NewReader(cl)}
}

func (c *conn) call(t *testing.T, send any) map[string]any {
	t.Helper()
	line, _ := json.Marshal(send)
	if _, err := c.c.Write(append(line, '\n')); err != nil {
		t.Fatal(err)
	}
	b, err := c.r.ReadBytes('\n')
	if err != nil {
		t.Fatal(err)
	}
	var reply map[string]any
	if err := json.Unmarshal(b, &reply); err != nil {
		t.Fatal(err)
	}
	return reply
}
