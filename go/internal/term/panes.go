package term

import (
	"strconv"
	"strings"
)

// Pane is one pane of the host's terminal server, as the debug view lists it.
type Pane struct {
	Session, Window, WindowName, Handle string
	PID                                 int    // the pane's own process (its shell); 0 when unknown
	Command                             string // what tmux says runs in the pane now
	Path                                string // the pane's working directory
	Activity                            int64  // the window's last output, unix seconds; 0 when unknown
	Dead                                bool
}

// PaneLister is a host that can list every pane it has with its details.
type PaneLister interface {
	ListPanes() ([]Pane, bool)
}

// paneFormat separates fields with a tab: a window name or a path may hold spaces, not a tab.
const paneFormat = "#{session_name}\t#{window_index}\t#{window_name}\t#{pane_id}\t#{pane_pid}\t#{pane_current_command}\t#{pane_current_path}\t#{window_activity}\t#{pane_dead}"

// ListPanes is every pane of the daemon's tmux server; ok is false when tmux could not answer. No server is no panes.
func (t Tmux) ListPanes() ([]Pane, bool) {
	code, out, err := t.tmux(tmuxTimeout, "", "list-panes", "-a", "-F", paneFormat)
	if code != 0 {
		low := strings.ToLower(err)
		for _, m := range noServer {
			if strings.Contains(low, m) {
				return []Pane{}, true
			}
		}
		return nil, false
	}
	return ParsePanes(out), true
}

// ParsePanes reads list-panes output in paneFormat; a line that is not one is skipped.
func ParsePanes(out string) []Pane {
	panes := []Pane{}
	for _, line := range strings.Split(out, "\n") {
		f := strings.Split(line, "\t")
		if len(f) != 9 || !paneRE.MatchString(f[3]) {
			continue
		}
		pid, _ := strconv.Atoi(f[4])
		act, _ := strconv.ParseInt(f[7], 10, 64)
		panes = append(panes, Pane{Session: f[0], Window: f[1], WindowName: f[2], Handle: f[3], PID: pid, Command: f[5], Path: f[6],
			Activity: act, Dead: f[8] == "1"})
	}
	return panes
}

func (d dynamic) ListPanes() ([]Pane, bool) {
	if l, ok := d.cur().(PaneLister); ok {
		return l.ListPanes()
	}
	return nil, false
}
