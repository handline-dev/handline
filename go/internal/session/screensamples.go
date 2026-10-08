package session

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// A sample of the screen of an agent that reports background work, kept to design against. What tells an idle agent with
// background work from one mid-turn is on its screen, and the sandboxes agents work in cannot read Orca; the daemon can. Each time
// it reads an agent's screen and finds background work, one sample in ten minutes goes to a file, labelled with what the db says
// the agent is doing. No extra read: the rows are the ones BackgroundWork just took. The newest SampleKeep files stay.
const (
	SampleIntervalS = 600
	SampleKeep      = 30
)

// SamplesDir is where the samples are kept.
func SamplesDir(home string) string { return filepath.Join(home, "diag", "screens") }

// RecordScreenSample writes one sample if it is due. True when written; a failure to write is not the tick's.
func (e *Env) RecordScreenSample(agent string, row *AgentRow, running string, tail []string) bool {
	now := e.now()
	if len(tail) == 0 || now-e.GetCursor("screen_sample:"+agent) < SampleIntervalS {
		return false
	}
	d := SamplesDir(e.Home)
	if os.MkdirAll(d, 0o777) != nil {
		return false
	}
	state, detail := "?", ""
	if row != nil {
		state, detail = row.State, row.Detail
	}
	head := []string{"agent: " + agent,
		"time: " + time.Unix(now, 0).Format("2006-01-02 15:04:05"),
		"db state: " + state, "db detail: " + detail,
		"session says: " + running,
		fmt.Sprintf("rows: %d (bottom of the screen, top first)", len(tail)), strings.Repeat("-", 60)}
	path := filepath.Join(d, fmt.Sprintf("%s-%d.txt", agent, now))
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600) // screens are private
	if err != nil {
		return false
	}
	_, err = f.WriteString(strings.Join(append(head, tail...), "\n") + "\n")
	if cerr := f.Close(); err != nil || cerr != nil {
		return false
	}
	files, _ := filepath.Glob(filepath.Join(d, "*.txt"))
	type aged struct {
		path string
		mod  time.Time
	}
	var all []aged
	for _, p := range files {
		if st, err := os.Stat(p); err == nil {
			all = append(all, aged{p, st.ModTime()})
		}
	}
	sort.SliceStable(all, func(i, j int) bool { return all[i].mod.Before(all[j].mod) })
	for len(all) > SampleKeep {
		if os.Remove(all[0].path) != nil {
			return false
		}
		all = all[1:]
	}
	return e.SetCursor("screen_sample:"+agent, now) == nil
}
