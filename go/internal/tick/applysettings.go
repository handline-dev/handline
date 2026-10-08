package tick

import (
	"encoding/json"
	"reflect"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/states"
)

// A session started with settings that have since changed is restarted, once, at an idle moment. What a launch reads from the settings a
// person edits (the model, effort, mcp config, browser, permission switches, ROLE.md, base.md, the team line and rules) is read once, at
// session start. The launch records each beside the session; this compares those with what the next launch would use and, when a named
// setting differs, restarts the agent the way `hl restart` does (resumed, the same conversation), never mid-turn and never over an open
// dialog. A level read each tick from the row, not an edge: a change made while the agent was busy is applied when it is not.
const (
	// SettingsWho is who the restart says asked for it.
	SettingsWho = "the daemon"
	// SettingsApplied is the meta key prefix that keeps what a restart was for.
	SettingsApplied = "settings_applied:"
)

// Part is one named setting a launch reads, in launch order.
type Part struct {
	Name  string
	Value any
}

// SettingsPorts are the foreign calls applying settings makes.
type SettingsPorts interface {
	// FingerprintParts is launch.fingerprint_parts: the settings the next launch would use, in launch order.
	FingerprintParts(agent string) ([]Part, error)
	// CurrentAccount is agents.current_account.
	CurrentAccount(q db.Q, agent string) string
}

// ApplySettings restarts sessions launched with settings that have changed.
type ApplySettings struct{ Ports SettingsPorts }

// Changed is the named settings the running session was launched without, in launch order; nothing for a session that recorded none (a
// launch from before they were kept).
func (a *ApplySettings) Changed(agent string, row *db.AgentState) ([]string, error) {
	var old map[string]json.RawMessage
	if err := json.Unmarshal([]byte(orNull(row.LaunchParts.String)), &old); err != nil || old == nil {
		return nil, nil
	}
	parts, err := a.Ports.FingerprintParts(agent)
	if err != nil {
		return nil, err
	}
	var names []string
	for _, p := range parts {
		was, ok := old[p.Name]
		if !ok {
			continue
		}
		now, err := json.Marshal(p.Value)
		if err != nil {
			return nil, err
		}
		if !sameJSON(was, now) {
			names = append(names, p.Name)
		}
	}
	return names, nil
}

func orNull(s string) string {
	if s == "" {
		return "null"
	}
	return s
}

func sameJSON(a, b []byte) bool {
	var x, y any
	if json.Unmarshal(a, &x) != nil || json.Unmarshal(b, &y) != nil {
		return false
	}
	return reflect.DeepEqual(x, y)
}

// Sweep applies changed settings to every agent; one agent's failure is not the tick's.
func (a *ApplySettings) Sweep(t *Tick, now int64) error {
	for _, agent := range t.Host.Agents() {
		if err := guard(func() { a.apply(t, agent) }); err != nil {
			t.logf("  %s: applying changed settings failed: %v", agent, err)
		}
	}
	return nil
}

func (a *ApplySettings) apply(t *Tick, agent string) {
	row := must(db.GetAgentState(t.Q, agent))
	if row == nil || closedAt(row) || row.Terminal.String == "" || (row.State != states.Idle && row.State != states.Queued) {
		return
	}
	names := must(a.Changed(agent, row))
	if len(names) == 0 {
		return
	}
	// What this restart is for, kept: a launch that still disagrees with the settings after it (it has happened with things a launch
	// cannot reproduce) is not restarted again for the same change.
	parts := must(a.Ports.FingerprintParts(agent))
	obj := map[string]any{}
	for _, p := range parts {
		obj[p.Name] = p.Value
	}
	text := rpc.PyJSON(obj, true, true)
	if applied, _ := must2(db.Meta(t.Q, SettingsApplied+agent)); applied == text {
		return
	}
	if !t.Delivery.SessionPresent(agent) || !t.Host.TermAvailable() || !t.Host.TypeableNow(row.Terminal.String) {
		return // busy, or a dialog is open: the next tick asks again
	}
	st := t.Usage.ReadForAgent(agent, a.Ports.CurrentAccount(t.Q, agent))
	if st.SessionID == "" {
		return
	}
	ok, said := t.Lifecycle.RestartAgent(t.Q, agent, st, SettingsWho, names, "settings")
	if ok {
		check(db.SetMeta(t.Q, SettingsApplied+agent, text))
		t.logf("  %s: restarted to apply changed settings (%s)", agent, strings.Join(names, ", "))
	} else {
		t.logf("  %s: changed settings (%s) not applied yet - %s", agent, strings.Join(names, ", "), said)
	}
}
