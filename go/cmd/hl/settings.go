package main

import (
	"fmt"
	"os"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/timefmt"
)

// The settings commands of cli/idle.py, the founder's path: settings.set writes, settings.get reads what is printed.
// From an agent's session a global switch is an HR proposal, and a terminal without the founder key is refused by
// both settings methods; both stay Python's.

// settingsCaller is whether this process may take the founder's path.
func settingsCaller(c *hlclient.Client) bool { return !protected.InAgentSession() && c.Key != "" }

// settingsView is settings.get: the global values, and with an agent its own overrides.
type settingsView struct{ values, overrides map[string]any }

func getSettings(c *hlclient.Client, agent string) (settingsView, error) {
	params := map[string]any{}
	if agent != "" {
		params["agent"] = agent
	}
	got, err := c.Call("settings.get", params)
	if err != nil {
		return settingsView{}, err
	}
	v, _ := got["values"].(map[string]any)
	o, _ := got["overrides"].(map[string]any)
	return settingsView{v, o}, nil
}

// setSettings writes changes (a nil value clears the key), for agent's own when one is named; "" when done, else why not.
func setSettings(c *hlclient.Client, agent string, changes map[string]any) string {
	params := map[string]any{"changes": changes}
	if agent != "" {
		params["agent"] = agent
	}
	if ok, said := c.Write("settings.set", params); !ok {
		return said
	}
	return ""
}

// seconds is a whole number of seconds from a JSON value, as Python's isinstance(v, int) and not bool: ok false otherwise.
func seconds(v any) (int64, bool) {
	f, ok := v.(float64)
	if !ok || f != float64(int64(f)) {
		return 0, false
	}
	return int64(f), true
}

// own is the agent's override of key when it is a whole number above floor, else the global value.
func (s settingsView) own(key string, floor int64) int64 {
	if n, ok := seconds(s.overrides[key]); ok && n > floor {
		return n
	}
	n, _ := seconds(s.values[key])
	return n
}

// failed prints a write or read that did not happen the way cli/idle.py prints its exception, and its exit code.
func failed(why string) int {
	fmt.Fprintf(os.Stderr, "error: %s\n", why)
	return 1
}

func clearNeedsAgent() int {
	fmt.Fprintln(os.Stderr, "--clear only applies with --agent")
	return 1
}

// idleBackground is the global form only: an agent's notice follows its own idle window unless config.yaml names one,
// and settings.get does not say whether it does, so --agent stays Python's.
func idleBackground(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--notice", "--grace", "--check"}, []string{"--clear"}, nil)
	if !ok || len(p.pos) != 0 || !settingsCaller(c) {
		return 0, false
	}
	changes := map[string]any{}
	for _, k := range []string{"notice", "grace", "check"} {
		if v, given := p.opt("--" + k); given {
			n, err := timefmt.ParseSpan(v)
			if err != nil {
				return 0, false
			}
			changes["bg_"+k+"_s"] = n
		}
	}
	c.NoteVerbChain([]string{"idle-background", ""})
	if p.flags["--clear"] {
		return clearNeedsAgent(), true
	}
	if len(changes) > 0 {
		if why := setSettings(c, "", changes); why != "" {
			return failed(why), true
		}
	}
	s, err := getSettings(c, "")
	if err != nil {
		return failed(err.Error()), true
	}
	n := func(k string) int64 { v, _ := seconds(s.values[k]); return v }
	fmt.Printf("default: asked which background work to stop every %s while any runs\n", timefmt.Ago(n("bg_check_s")))
	if n("park_timeout_s") == 0 {
		fmt.Println("default: never told - its idle window is 'never close'")
		return 0, true
	}
	fmt.Printf("default: told to finish or stop its background work after %s of nothing shown, and it is stopped %s after that\n",
		timefmt.Ago(n("bg_notice_s")), timefmt.Ago(n("bg_grace_s")))
	return 0, true
}

func noticeDigest(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--agent", "--why"}, []string{"--clear"}, nil)
	if !ok || len(p.pos) > 1 || !settingsCaller(c) {
		return 0, false
	}
	var span any
	if len(p.pos) == 1 {
		n, err := timefmt.ParseSpan(p.pos[0])
		if err != nil {
			return 0, false
		}
		span = n
	}
	c.NoteVerbChain([]string{"notice-digest", ""})
	agent, _ := p.opt("--agent")
	if p.flags["--clear"] && agent == "" {
		return clearNeedsAgent(), true
	}
	if p.flags["--clear"] || span != nil {
		if p.flags["--clear"] {
			span = nil
		}
		if why := setSettings(c, agent, map[string]any{"notice_digest_s": span}); why != "" {
			return failed(why), true
		}
	}
	s, err := getSettings(c, agent)
	if err != nil {
		return failed(err.Error()), true
	}
	who, secs := agent, s.own("notice_digest_s", 0)
	if who == "" {
		who = "default"
		secs, _ = seconds(s.values["notice_digest_s"])
	}
	fmt.Printf("%s: notices are told in one digest at its prompt, from %s after the first and never mid-turn (sooner with any message); "+
		"a failure, block or FATAL at once\n", who, timefmt.Ago(secs))
	return 0, true
}
