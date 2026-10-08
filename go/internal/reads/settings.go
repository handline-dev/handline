package reads

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/inputhold"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/usage"
)

// The numbers the settings screen shows as defaults (timebox.py, delivery.py): the daemon's own constants, not config.
const (
	unaskedRunningS   = 900
	stopWaitBackstopS = 900
	staleBackgroundS  = 3000
)

// perAgent is rpc_settings.PER_AGENT: the settings an agent.yaml may override.
var perAgent = []string{"park_timeout_s", "ignore_cache_expiry", "bg_notice_s", "bg_grace_s", "bg_check_s", "notice_digest_s"}

// hooksWanted is session/events.py's CAPTURED, ANSWERS and GUARDS: the events our hook is installed for.
var hooksWanted = []string{"SessionStart", "SessionEnd", "Stop", "StopFailure", "SubagentStop", "PostCompact", "UserPromptSubmit",
	"PermissionRequest", "TeammateIdle", "PostToolUse", "PostToolUseFailure", "PreToolUse"}

// ourCommands is names.COMMANDS: what the installed command may be called, whatever path it was written with.
var ourCommands = map[string]bool{"hl": true, "handline": true, "ocp": true}

// isOurHook is claude_hooks._is_our_hook: `<exe> hook <event>`, the exe being one of ours at any path.
func isOurHook(command any, event string) bool {
	s, ok := command.(string)
	if !ok {
		return false
	}
	parts := strings.Fields(s)
	return len(parts) >= 3 && parts[len(parts)-2] == "hook" && parts[len(parts)-1] == event && ourCommands[filepath.Base(parts[0])]
}

// hooksInstalled is rpc_lists._hooks_installed: every event wanted has an entry of ours in the shared settings. A settings file that
// cannot be read is "not installed", not a failed call.
func (e *Env) hooksInstalled() bool {
	b, err := os.ReadFile(filepath.Join(e.Home, "shared", ".claude", "settings.json"))
	var data map[string]any
	if err != nil || json.Unmarshal(b, &data) != nil || data == nil {
		data = map[string]any{}
	}
	hooks, _ := data["hooks"].(map[string]any)
	have := map[string]bool{}
	for event, groups := range hooks {
		list, ok := groups.([]any)
		if !ok {
			continue
		}
	groups:
		for _, g := range list {
			gm, ok := g.(map[string]any)
			if !ok {
				continue
			}
			inner, _ := gm["hooks"].([]any)
			for _, h := range inner {
				if hm, ok := h.(map[string]any); ok && isOurHook(hm["command"], event) {
					have[event] = true
					break groups
				}
			}
		}
	}
	for _, w := range hooksWanted {
		if !have[w] {
			return false
		}
	}
	return true
}

// declaredWorktrees is details.eligible_worktrees' declared half: every registered agent's worktrees inside a root the founder declared.
func (e *Env) declaredWorktrees() ([][2]string, error) {
	trust := team.NewTrust(e.Home)
	var out [][2]string
	for _, name := range paths.ListAgents(e.Home) {
		row, err := db.GetAgentState(e.DB, name)
		if err != nil {
			return nil, err
		}
		for _, wt := range lifecycle.AllPaths(row) {
			if trust.IsDeclared(wt) {
				out = append(out, [2]string{name, wt})
			}
		}
	}
	return out, nil
}

// settingsValues is rpc_lists._values: every setting the screen shows, with what it stands at now.
func (e *Env) settingsValues() map[string]any {
	cfg := usage.LoadConfig(e.Home)
	s := &lifecycle.Settings{Home: e.Home}
	park := s.DefaultCloseTimeoutS()
	bgNotice := park
	if bgNotice == 0 {
		bgNotice = 1
	}
	prices := map[string]any{}
	if m, ok := cfg["token_prices"].(map[string]any); ok {
		for k, v := range m {
			prices[k] = v
		}
	}
	return map[string]any{
		"active_cap": cfg.DefaultCap(), "park_timeout_s": park, "handoff_note": s.Flag("handoff_note", true),
		"auto_accept_dialogs": s.Flag("auto_accept_dialogs", true), "ignore_cache_expiry": cfg["ignore_cache_expiry"] == true,
		"dev_mode":    truthy(cfg["dev_mode"]),
		"bg_notice_s": s.ConfigSeconds("bg_notice_s", bgNotice), "bg_grace_s": s.ConfigSeconds("bg_grace_s", lifecycle.BgGraceS),
		"bg_check_s": s.ConfigSeconds("bg_check_s", lifecycle.BgCheckS), "notice_digest_s": s.ConfigSeconds("notice_digest_s", lifecycle.NoticeDigestS),
		"unasked_running_s":    s.ConfigSeconds("unasked_running_s", unaskedRunningS),
		"stop_wait_backstop_s": s.ConfigSeconds("stop_wait_backstop_s", stopWaitBackstopS),
		"stale_background_s":   s.ConfigSeconds("stale_background_s", staleBackgroundS), "token_prices": prices,
		"input_hold_idle_s": inputhold.IdleSeconds(e.Home),
	}
}

// settingsGet is settings.get: timings, terminal host, listener, trust folders, hooks and every value.
func (e *Env) settingsGet(_ *rpc.Conn, params map[string]any) (any, error) {
	agent, err := e.chatAgent(params, false)
	if err != nil {
		return nil, err
	}
	values := e.settingsValues()
	declared, err := e.declaredWorktrees()
	if err != nil {
		return nil, err
	}
	installed := e.hooksInstalled()
	names := paths.ListAccounts(e.Home)
	timings := map[string]any{}
	for k, v := range values {
		if strings.HasSuffix(k, "_s") {
			timings[k] = v
		}
	}
	folders := []any{}
	accounts := []any{}
	for _, a := range names {
		for _, d := range declared {
			folders = append(folders, map[string]any{"account": a, "agent": d[0], "path": d[1]})
		}
		accounts = append(accounts, map[string]any{"account": a, "installed": installed})
	}
	out := map[string]any{
		"timings": timings,
		// This daemon drives no terminal host yet, as daemon.status says.
		"terminal_host": map[string]any{"choice": "none", "available": []any{}, "health": "down"},
		"listener":      map[string]any{"enabled": false, "bind": "loopback", "encryption": "none", "fingerprint": nil},
		"trust_folders": folders,
		"hooks":         map[string]any{"installed": installed, "accounts": accounts},
		"values":        values,
	}
	if agent != "" {
		cfg, _ := team.NewRoster(e.Home).Read(agent)
		over := map[string]any{}
		for _, k := range perAgent {
			if v, ok := cfg[k]; ok {
				over[k] = v
			}
		}
		out["overrides"] = over
	}
	return out, nil
}
