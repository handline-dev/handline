package main

import (
	"fmt"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/timefmt"
)

// handoffCmd is `hl handoff slow`: the 'Slowed:' lines of the archived handoff notes, the ones several notes report first, then each
// agent's, newest first. It reads the archive files; the daemon is not asked.
func handoffCmd(c *hlclient.Client, args []string) (int, bool) {
	if len(args) == 0 || args[0] != "slow" {
		return 0, false
	}
	p, ok := parse(args[1:], []string{"--since", "--agent"}, nil, nil)
	if !ok || len(p.pos) != 0 {
		return 0, false
	}
	span := int64(7 * 24 * 3600)
	if v, has := p.opt("--since"); has {
		secs, err := timefmt.ParseSpan(v)
		if err != nil {
			return wrongArgs("--since is a span like 2h or 7d")
		}
		span = secs
	}
	agent, _ := p.opt("--agent")
	lines, err := handoff.SlowedLines(c.Home, agent, time.Now().Add(-time.Duration(span)*time.Second))
	if err != nil {
		fmt.Fprintf(os.Stderr, "could not read the handoff archive: %v\n", err)
		return 1, true
	}
	if len(lines) == 0 {
		fmt.Println("no Slowed: lines in the handoff notes of that span")
		return 0, true
	}
	fmt.Print(slowReport(lines))
	return 0, true
}

// slowReport is lines (newest first) as text: the lines more than one note gave, most often first, then each agent's.
func slowReport(lines []handoff.Slowed) string {
	key := func(l string) string { return strings.ToLower(strings.Join(strings.Fields(l), " ")) }
	count, who := map[string]int{}, map[string]map[string]bool{}
	for _, s := range lines {
		k := key(s.Line)
		count[k]++
		if who[k] == nil {
			who[k] = map[string]bool{}
		}
		who[k][s.Agent] = true
	}
	var b strings.Builder
	var repeated []handoff.Slowed
	seen := map[string]bool{}
	for _, s := range lines {
		if k := key(s.Line); count[k] > 1 && !seen[k] {
			seen[k] = true
			repeated = append(repeated, s)
		}
	}
	sort.SliceStable(repeated, func(i, j int) bool { return count[key(repeated[i].Line)] > count[key(repeated[j].Line)] })
	if len(repeated) > 0 {
		b.WriteString("reported more than once:\n")
		for _, s := range repeated {
			k := key(s.Line)
			fmt.Fprintf(&b, "  %dx by %d agent(s)  %s\n", count[k], len(who[k]), textsafe.Text(s.Line))
		}
		b.WriteString("\n")
	}
	var agents []string
	by := map[string][]handoff.Slowed{}
	for _, s := range lines {
		if by[s.Agent] == nil {
			agents = append(agents, s.Agent) // in the order of each agent's newest line
		}
		by[s.Agent] = append(by[s.Agent], s)
	}
	for _, a := range agents {
		b.WriteString(a + "\n")
		for _, s := range by[a] {
			fmt.Fprintf(&b, "  %s  %s\n", s.At.Format("01-02 15:04"), textsafe.Text(s.Line))
		}
	}
	return b.String()
}
