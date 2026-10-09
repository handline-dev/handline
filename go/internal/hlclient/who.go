package hlclient

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/sessiontoken"
	_ "modernc.org/sqlite"
)

// Agent is $OCP_AGENT as given, "" when unset.
func Agent() string {
	v, _ := paths.Getenv("AGENT")
	return v
}

// CurrentAgent is who is asking (ask.current_agent): $OCP_AGENT, except under a Claude Code that itself runs under
// another, which inherited the name without being that agent and is nobody; that refusal is recorded.
func (c *Client) CurrentAgent(argv []string) string {
	agent := Agent()
	if agent != "" && sessiontoken.New(c.Home).Nested(0) {
		c.record(agent, "nested_claude_refused", argv)
		return ""
	}
	return agent
}

// WhoRefusal is why a write cannot be made as a caller that cannot be named (ask._who_refusal).
func WhoRefusal(verb string) string {
	if a := Agent(); a != "" {
		return fmt.Sprintf("refused: this runs under a second Claude Code started inside an agent's session, which "+
			"inherited the name %s without being that agent; only the agent's own session can %s as it", a, verb)
	}
	return "cannot tell who is writing: OCP_AGENT is unset"
}

// NoteVerbChain records a state-changing command and the programs above it (ask.note_verb_chain), so a command whose
// caller the ancestry check could not tell apart can still be traced. A record that cannot be written changes nothing.
func (c *Client) NoteVerbChain(argv []string) {
	if a := Agent(); a != "" {
		c.record(a, "verb_chain", argv)
	}
}

func (c *Client) record(agent, kind string, argv []string) {
	short := make([]string, 0, 3)
	for _, a := range argv {
		if len(short) == 3 {
			break
		}
		if r := []rune(a); len(r) > 40 {
			a = string(r[:40])
		}
		short = append(short, a)
	}
	var chain []string
	for i, a := range sessiontoken.New(c.Home).Chain(0) {
		if i == 8 {
			break
		}
		chain = append(chain, fmt.Sprintf("%s:%d", a.Program, a.PID))
	}
	// No migration and no new file from a command: a database this cannot write to as it stands is left alone.
	con, err := sql.Open("sqlite", "file:"+paths.DBFile(c.Home)+"?mode=rw&_pragma=busy_timeout(5000)")
	if err != nil {
		return
	}
	defer con.Close()
	_, _ = db.AddEvent(con, agent, kind, db.KV{K: "argv", V: short}, db.KV{K: "chain", V: strings.Join(chain, " > ")})
}
