package session

import (
	"encoding/json"
	"io"
	"os"
	"regexp"
	"strings"

	"github.com/handline/handline/go/internal/rpc"
)

// Which background commands an agent has running, and for how long. The footer says only how many ("2 shells"). The Bash hook,
// after a call made with run_in_background, has the command; Claude writes each job's output to <session>/tasks/<id>.output and
// ends it with "[exited with code N]". Note records the command when the call returns; Running is what is still going: started
// since the footer last showed none, no exit line in its output, and no more of them than the footer counts (a job that was
// killed leaves no exit line).

// BackgroundKind is the event Note writes.
const BackgroundKind = "background_started"

// CommandWidth is how much of a job's command Line shows.
const CommandWidth = 100

const exitMark = "[exited with code"

var (
	outputRE = regexp.MustCompile(`(/[^\s"'\\]+/tasks/([A-Za-z0-9]+)\.output)`)
	idRE     = regexp.MustCompile(`(?:backgroundTaskId|shellId|bash_id|task_id)"\s*:\s*"([A-Za-z0-9]+)"|ID:\s*([A-Za-z0-9]+)`)
	countRE  = regexp.MustCompile(`\d+`)
)

// NoteBackground records a Bash call that started something in the background. Every tool call runs this, so a call that is not
// one returns before anything is opened.
func (e *Env) NoteBackground(agent string, raw map[string]any) error {
	in, _ := raw["tool_input"].(map[string]any)
	if name, _ := raw["tool_name"].(string); name != "Bash" || in == nil || in["run_in_background"] != true {
		return nil
	}
	var text string
	if s, ok := raw["tool_response"].(string); ok {
		text = s
	} else {
		text = rpc.PyJSON(raw["tool_response"], false, false)
	}
	var job, output any
	if m := outputRE.FindStringSubmatch(text); m != nil {
		output, job = m[1], m[2]
	} else if m := idRE.FindStringSubmatch(text); m != nil {
		job = m[1]
		if m[1] == "" {
			job = m[2]
		}
	}
	command, _ := in["command"].(string)
	return e.AddEvent(agent, BackgroundKind, map[string]any{"command": command, "job": job, "output": output,
		"tool_use_id": raw["tool_use_id"], "session": raw["session_id"]})
}

// FooterCount is how many things the footer says are running: "2 shells, 1 monitor" is 3.
func FooterCount(text string) int {
	n := 0
	for _, d := range countRE.FindAllString(text, -1) {
		v := 0
		for _, c := range d {
			v = v*10 + int(c-'0')
		}
		n += v
	}
	return n
}

func ended(output string) bool {
	if output == "" {
		return false
	}
	f, err := os.Open(output)
	if err != nil {
		return false
	}
	defer f.Close()
	end, err := f.Seek(0, io.SeekEnd)
	if err != nil {
		return false
	}
	if _, err := f.Seek(max(0, end-200), io.SeekStart); err != nil {
		return false
	}
	b, err := io.ReadAll(f)
	return err == nil && strings.Contains(strings.ToValidUTF8(string(b), "\ufffd"), exitMark)
}

// BgJob is a recorded background command.
type BgJob struct {
	Command string
	Started int64
	Job     string // "" when the call named none
}

// RunningJobs is the recorded jobs still going, oldest first; with count above zero, only the last count of them.
func (e *Env) RunningJobs(agent string, count int) ([]BgJob, error) {
	since := max(e.LastEventTS(agent, "background_work_ended"), e.LastEventTS(agent, "SessionStart"))
	rows, err := e.DB.Query("SELECT ts, payload FROM events WHERE agent = ? AND kind = ? AND ts >= ? ORDER BY id", agent, BackgroundKind, since)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var jobs []BgJob
	for rows.Next() {
		var ts int64
		var payload *string
		if err := rows.Scan(&ts, &payload); err != nil {
			return nil, err
		}
		p := map[string]any{}
		if payload != nil && *payload != "" {
			if json.Unmarshal([]byte(*payload), &p) != nil {
				p = map[string]any{}
			}
		}
		out, _ := p["output"].(string)
		if ended(out) {
			continue
		}
		cmd, _ := p["command"].(string)
		job, _ := p["job"].(string)
		jobs = append(jobs, BgJob{cmd, ts, job})
	}
	if count > 0 && len(jobs) > count {
		jobs = jobs[len(jobs)-count:]
	}
	return jobs, rows.Err()
}

// Line is the job in one line: its command, cut to CommandWidth, and how long it has run.
func (j BgJob) Line(now int64, ago func(secs int64) string) string {
	cmd := strings.Join(strings.Fields(j.Command), " ")
	if r := []rune(cmd); len(r) > CommandWidth {
		cmd = string(r[:CommandWidth-1]) + "…"
	}
	return "`" + cmd + "` for " + ago(max(0, now-j.Started))
}
