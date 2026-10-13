package tick

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

type settingsPorts struct {
	parts   []Part
	account string
}

func (s settingsPorts) FingerprintParts(string) ([]Part, error) { return s.parts, nil }
func (s settingsPorts) CurrentAccount(db.Q, string) string      { return s.account }

// settled is a world whose agent "a" is open and idle, launched with the model "sonnet" and now configured for "opus".
func settled(t *testing.T) (*Tick, *world, *ApplySettings) {
	tk, w := newWorld(t)
	setState(t, tk, "a", states.Idle, 0)
	if _, err := tk.Q.Exec(`UPDATE agent_state SET launch_parts = '{"model": "sonnet", "effort": 3}' WHERE agent = 'a'`); err != nil {
		t.Fatal(err)
	}
	w.idle, w.restartOK = true, true
	w.status = Status{SessionID: "s1"}
	return tk, w, &ApplySettings{Ports: settingsPorts{parts: []Part{{"model", "opus"}, {"effort", float64(3)}}, account: "acct"}}
}

func TestAChangedSettingRestartsTheIdleSessionOnceAndRemembersWhatItWasFor(t *testing.T) {
	tk, w, a := settled(t)
	a.Sweep(tk, 10_000)
	if !w.did("restart a by the daemon for model (settings_applied)") || !w.logged("restarted to apply changed settings (model)") {
		t.Fatalf("%v %v", w.calls, w.logs)
	}
	if v, _, _ := db.Meta(tk.Q, SettingsApplied+"a"); !strings.Contains(v, `"model"`) || !strings.Contains(v, "opus") {
		t.Fatalf("what the restart was for was not kept: %q", v)
	}
	// A launch that still disagrees afterwards is not restarted again for the same change.
	w.calls = nil
	a.Sweep(tk, 10_030)
	if w.did("restart") {
		t.Fatalf("restarted twice for one change: %v", w.calls)
	}
	// A different change is restarted for.
	a.Ports = settingsPorts{parts: []Part{{"model", "opus"}, {"effort", float64(2)}}, account: "acct"}
	a.Sweep(tk, 10_060)
	if !w.did("restart a by the daemon for model,effort (settings_applied)") && !w.did("restart a by the daemon for effort (settings_applied)") {
		t.Fatalf("%v", w.calls)
	}
}

func TestAChangedSettingWaitsForAnIdleMomentAndAnOpenRunnableSession(t *testing.T) {
	cases := map[string]func(tk *Tick, w *world){
		"mid-turn or an open prompt": func(tk *Tick, w *world) { w.idle = false },
		"no session to restart":      func(tk *Tick, w *world) { w.alive["a"] = false },
		"session not present":        func(tk *Tick, w *world) { w.present = false },
		"running":                    func(tk *Tick, w *world) { setState(t, tk, "a", states.Running, 0) },
		"parked":                     func(tk *Tick, w *world) { setState(t, tk, "a", states.Parked, 20_000) },
		"closed":                     func(tk *Tick, w *world) { db.SetClosed(tk.Q, "a", "s1") },
		"no session id to resume":    func(tk *Tick, w *world) { w.status.SessionID = "" },
	}
	for name, mutate := range cases {
		tk, w, a := settled(t)
		mutate(tk, w)
		a.Sweep(tk, 10_000)
		if w.did("restart") {
			t.Errorf("%s: restarted", name)
		}
	}
	// A launch that recorded no parts (from before they were kept) is not restarted.
	tk, w, a := settled(t)
	tk.Q.Exec(`UPDATE agent_state SET launch_parts = NULL WHERE agent = 'a'`)
	a.Sweep(tk, 10_000)
	if w.did("restart") {
		t.Error("a launch with no recorded parts was restarted")
	}
}

func TestARestartThatDidNotHappenIsLoggedAndTriedAgainNextTick(t *testing.T) {
	tk, w, a := settled(t)
	w.restartOK, w.restartSaid = false, "mid-turn"
	a.Sweep(tk, 10_000)
	if !w.logged("changed settings (model) not applied yet - mid-turn") {
		t.Fatalf("%v", w.logs)
	}
	if v, _, _ := db.Meta(tk.Q, SettingsApplied+"a"); v != "" {
		t.Fatalf("a restart that failed was remembered as applied: %q", v)
	}
	w.restartOK = true
	a.Sweep(tk, 10_030)
	if v, _, _ := db.Meta(tk.Q, SettingsApplied+"a"); v == "" {
		t.Fatal("the next tick did not apply it")
	}
}

func TestOneAgentsFailureApplyingSettingsDoesNotStopTheSweep(t *testing.T) {
	tk, w, a := settled(t)
	w.agents = []string{"b", "a"} // b has no row at all
	a.Ports = failingFor{settingsPorts: a.Ports.(settingsPorts), bad: "b"}
	a.Sweep(tk, 10_000)
	if !w.did("restart a") {
		t.Fatalf("%v %v", w.calls, w.logs)
	}
}

type failingFor struct {
	settingsPorts
	bad string
}

func (f failingFor) FingerprintParts(agent string) ([]Part, error) {
	if agent == f.bad {
		panic("agent.yaml unreadable")
	}
	return f.settingsPorts.FingerprintParts(agent)
}
