package compose

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/structured"
)

func TestOnlyATerminalAgentWhoseYAMLSaysPeerInboxIsDeliveredThroughIt(t *testing.T) {
	home := t.TempDir()
	for agent, yaml := range map[string]string{"term": "peer_inbox: true # inbox\n", "plain": "mode: auto\n", "strm": "structured: true\npeer_inbox: true\n"} {
		dir := filepath.Join(paths.AgentsDir(home), agent)
		os.MkdirAll(dir, 0o755)
		os.WriteFile(filepath.Join(dir, "agent.yaml"), []byte(yaml), 0o644)
	}
	p := New(Config{Home: home})
	port := p.agentsPort()
	if !port.Structured("term") || port.Structured("plain") || !port.Structured("strm") {
		t.Fatal("term and strm are delivered by a send path; plain is typed at")
	}
	// term goes to the peer port: it has no record to find, so nothing is written; strm goes to the (empty) structured manager
	if _, err := port.Send("term", "op", "hi", structured.Now); err == nil {
		t.Fatal("a peer agent with no session")
	}
	if port.Live("term") || port.Live("strm") {
		t.Fatal("live")
	}
}
