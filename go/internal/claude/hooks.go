package claude

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/handline/handline/go/internal/atomicfile"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
)

// The hook events a launch keeps installed: captured, the answers to a permission prompt, and the guard
// (events.CAPTURED | ANSWERS | GUARDS).
var Wanted = []string{"SessionStart", "SessionEnd", "Stop", "StopFailure", "SubagentStop", "PostCompact", "UserPromptSubmit",
	"PermissionRequest", "TeammateIdle", "PostToolUse", "PostToolUseFailure", "PreToolUse"}

// matchers limit a hook of ours to the tools it looks at, for the events that fire on every call.
var matchers = map[string]string{"PreToolUse": "Write|Edit|MultiEdit|NotebookEdit|Bash"}

var (
	oldStatusLines     = []string{"statusline.sh", "statusline-collect.sh"}
	quietNotifications = []string{"inputNeededNotifEnabled", "agentPushNotifEnabled"}
	orcaMarkers        = []string{"orca/agent-hooks", ".orca/agent-hooks", "ORCA_AGENT_HOOK", "orca-hook"}
	ocpCommands        = []string{"hl", "handline", "ocp"}
)

// Hooks is the shared settings file every account's settings.json links to (claude_hooks.py).
type Hooks struct{ Home string }

var _ lifecycle.Hooks = Hooks{}

func (h Hooks) shared() string { return filepath.Join(h.Home, "shared", ".claude") }

// Target is the shared settings file.
func (h Hooks) Target() string { return filepath.Join(h.shared(), "settings.json") }

// readObject is the settings file as an object, a new one when there is none; an error, changing nothing, when it is
// there but not readable JSON or not an object, since it holds the founder's settings too.
func (h Hooks) readObject() (*Object, error) {
	b, err := os.ReadFile(h.Target())
	if errors.Is(err, os.ErrNotExist) {
		return NewObject(), nil
	}
	if err != nil {
		return nil, fmt.Errorf("%s could not be read (%v); ocp changed nothing", h.Target(), err)
	}
	v, err := Decode(string(b))
	if err != nil {
		return nil, fmt.Errorf("%s could not be read (%v); ocp changed nothing", h.Target(), err)
	}
	o, ok := v.(*Object)
	if !ok {
		return nil, fmt.Errorf("%s is not a JSON object; ocp changed nothing", h.Target())
	}
	return o, nil
}

// write replaces the settings file in one step, as json.dumps(data, indent=2, ensure_ascii=False) writes it.
func (h Hooks) write(data *Object) error {
	if err := os.MkdirAll(h.shared(), 0o755); err != nil {
		return err
	}
	return atomicfile.Write(h.Target(), Dumps(data, 2, false)+"\n")
}

// EnsureShared makes shared/.claude and its settings: ocp's old status line taken out, Claude Code's auto-continue and
// its notifications off unless the file says otherwise (claude_hooks.ensure_shared).
func (h Hooks) EnsureShared() error {
	_, statErr := os.Stat(h.Target())
	current, err := h.readObject()
	if err != nil {
		return fmt.Errorf("%w. Fix or move it, then run this again", err)
	}
	for _, sub := range []string{"", "skills", "agents", "commands"} {
		if err := os.MkdirAll(filepath.Join(h.shared(), sub), 0o755); err != nil {
			return err
		}
	}
	var ours []string
	for _, n := range oldStatusLines {
		ours = append(ours, filepath.Join(h.shared(), n))
	}
	changed := false
	if line, ok := current.vals["statusLine"].(*Object); ok {
		if cmd, ok := line.vals["command"].(string); ok && slices.Contains(ours, cmd) {
			current.Delete("statusLine")
			changed = true
		}
	}
	for _, k := range append([]string{"autoContinueAtUsageLimit"}, quietNotifications...) {
		if _, has := current.Get(k); !has {
			current.Set(k, false)
			changed = true
		}
	}
	if statErr != nil || changed {
		if err := h.write(current); err != nil {
			return err
		}
	}
	for _, p := range ours { // only once nothing points at them
		_ = os.Remove(p)
	}
	return nil
}

// isOurs is whether a hook command is ours, whatever path it was written with: `<hl|handline|ocp> ... hook <event>`.
func isOurs(command any, event string) bool {
	s, ok := command.(string)
	if !ok {
		return false
	}
	parts := strings.Fields(s)
	n := len(parts)
	return n >= 3 && parts[n-2] == "hook" && parts[n-1] == event && slices.Contains(ocpCommands, filepath.Base(parts[0]))
}

func objects(v any) []*Object {
	list, _ := v.([]any)
	out := make([]*Object, 0, len(list))
	for _, e := range list {
		if o, ok := e.(*Object); ok {
			out = append(out, o)
		}
	}
	return out
}

// Missing is whether a wanted event has no entry of ours in the shared settings (claude_hooks.missing).
func (h Hooks) Missing() bool {
	data, err := h.readObject()
	if err != nil {
		return true
	}
	hooks, _ := data.vals["hooks"].(*Object)
	for _, event := range Wanted {
		found := false
		if hooks != nil {
			for _, g := range objects(hooks.vals[event]) {
				for _, e := range objects(g.vals["hooks"]) {
					if isOurs(e.vals["command"], event) {
						found = true
					}
				}
			}
		}
		if !found {
			return true
		}
	}
	return false
}

func isOrca(e *Object) bool {
	cmd, ok := e.vals["command"].(string)
	if !ok {
		return false
	}
	for _, m := range orcaMarkers {
		if strings.Contains(cmd, m) {
			return true
		}
	}
	return false
}

// stripOrca drops the Orca hooks the old mirror wrote, leaving everything else; each group it keeps is rebuilt with its
// "hooks" last, as Python's rebuild leaves it.
func stripOrca(data *Object) {
	hooks, ok := data.vals["hooks"].(*Object)
	if !ok {
		return
	}
	kept := NewObject()
	for _, event := range hooks.keys {
		groups, ok := hooks.vals[event].([]any)
		if !ok {
			kept.Set(event, hooks.vals[event])
			continue
		}
		var keep []any
		for _, g := range groups {
			o, ok := g.(*Object)
			if !ok {
				keep = append(keep, g)
				continue
			}
			var entries []any
			list, _ := o.vals["hooks"].([]any)
			for _, e := range list {
				if eo, ok := e.(*Object); ok && isOrca(eo) {
					continue
				}
				entries = append(entries, e)
			}
			if len(entries) > 0 {
				ng := NewObject()
				for _, k := range o.keys {
					if k != "hooks" {
						ng.Set(k, o.vals[k])
					}
				}
				ng.Set("hooks", entries)
				keep = append(keep, ng)
			}
		}
		if len(keep) > 0 {
			kept.Set(event, keep)
		}
	}
	if kept.Len() > 0 {
		data.Set("hooks", kept)
	} else {
		data.Delete("hooks")
	}
}

// Install adds `exe hook <event>` for each wanted event to the shared settings, collapses ours in any spelling to one
// entry, and drops the Orca hooks the old mirror wrote (claude_hooks.install).
func (h Hooks) Install(exe string) error {
	data, err := h.readObject()
	if err != nil {
		return err
	}
	stripOrca(data)
	block, ok := data.setDefault("hooks", NewObject()).(*Object)
	if !ok {
		return fmt.Errorf("%s: hooks is not an object", h.Target())
	}
	events := slices.Clone(Wanted)
	for _, k := range block.keys {
		if !slices.Contains(events, k) {
			events = append(events, k)
		}
	}
	slices.Sort(events)
	for _, event := range events {
		wanted := slices.Contains(Wanted, event)
		raw, present := block.Get(event)
		if !present {
			if !wanted {
				continue
			}
			raw = []any{}
			block.Set(event, raw)
		}
		groups, ok := raw.([]any)
		if !ok {
			return fmt.Errorf("%s: hooks.%s is not a list", h.Target(), event)
		}
		var keep []any
		seen := false
		for _, g := range groups {
			o, ok := g.(*Object)
			if !ok {
				keep = append(keep, g)
				continue
			}
			entries, _ := o.vals["hooks"].([]any)
			var mine, others []any
			for _, e := range entries {
				if eo, ok := e.(*Object); ok && isOurs(eo.vals["command"], event) {
					mine = append(mine, e)
				} else {
					others = append(others, e)
				}
			}
			if len(mine) == 0 {
				keep = append(keep, g)
				continue
			}
			if seen || len(entries) > len(mine) {
				if others == nil {
					others = []any{}
				}
				o.Set("hooks", others)
				if len(others) > 0 {
					keep = append(keep, o)
				}
				continue
			}
			seen = true
			if m, ok := matchers[event]; ok {
				o.Set("matcher", m)
			}
			keep = append(keep, o)
		}
		if !seen && wanted {
			g := NewObject()
			if m, ok := matchers[event]; ok {
				g.Set("matcher", m)
			}
			entry := NewObject()
			entry.Set("type", "command")
			entry.Set("command", exe+" hook "+event)
			entry.Set("timeout", 10)
			g.Set("hooks", []any{entry})
			keep = append(keep, g)
		}
		if keep == nil {
			keep = []any{}
		}
		block.Set(event, keep)
	}
	return h.write(data)
}

// AllowHomeSocket lets a sandboxed agent connect to the daemon's socket: its path in sandbox.network.allowUnixSockets,
// once (claude_hooks.allow_home_socket).
func (h Hooks) AllowHomeSocket() error {
	sock := paths.SocketFile(h.Home)
	data, err := h.readObject()
	if err != nil {
		return err
	}
	network := data.block("sandbox").block("network")
	allowed, ok := network.vals["allowUnixSockets"].([]any)
	if !ok {
		allowed = []any{}
	}
	want := paths.Realpath(sock)
	for _, e := range allowed {
		if s, ok := e.(string); ok && paths.Realpath(expandUser(s)) == want {
			return nil
		}
	}
	network.Set("allowUnixSockets", append(allowed, sock))
	return h.write(data)
}

// expandUser is Path.expanduser for `~` and `~/...`.
func expandUser(p string) string {
	if p != "~" && !strings.HasPrefix(p, "~/") {
		return p
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return p
	}
	return home + p[1:]
}
