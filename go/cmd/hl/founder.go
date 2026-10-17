package main

import (
	"bufio"
	"fmt"
	"net/url"
	"os"
	"strings"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/textsafe"
)

// The founder's own verbs: device, cancel and team seal. The daemon refuses each to an agent session, so they need no check here.

func line(v any) string { return textsafe.Oneline(fmt.Sprint(v)) }

// device is `hl device pair | list | revoke`.
func device(c *hlclient.Client, args []string) (int, bool) {
	if len(args) == 0 {
		return 0, false
	}
	switch args[0] {
	case "pair":
		p, ok := parse(args[1:], []string{"--name"}, nil, nil)
		if !ok || len(p.pos) != 0 {
			return 0, false
		}
		params := map[string]any{}
		if name, _ := p.opt("--name"); name != "" {
			params["name"] = name
		}
		ok, said, got := c.Written("device.pair", params)
		if !ok {
			fmt.Fprintln(os.Stderr, said)
			return 1, true
		}
		uri := line(got["uri"])
		fmt.Printf("code: %s   (one time, expires %s)\n", line(got["code"]), line(got["expires_at"]))
		fmt.Printf("uri: %s\n", uri)
		if u, err := url.Parse(uri); err == nil && u.Query().Get("host") != "" {
			fmt.Printf("address: %s\n", line(u.Query().Get("host")))
		}
		if fp := line(orEmpty(got["fingerprint"])); fp != "" {
			fmt.Printf("fingerprint: %s\n", fp)
		} else {
			fmt.Println("fingerprint: none (the gateway runs plain on the Tailscale address; the tailnet encrypts it)")
		}
		return 0, true
	case "list":
		if len(args) != 1 {
			return 0, false
		}
		got, err := c.Call("devices.list", nil)
		if err != nil {
			fmt.Fprintln(os.Stderr, "devices.list:", err)
			return 1, true
		}
		devices := rows(got["devices"])
		if len(devices) == 0 {
			fmt.Println("no device is paired")
			return 0, true
		}
		width := 0
		for _, d := range devices {
			width = max(width, len(line(d["id"])))
		}
		for _, d := range devices {
			state, seen := "offline", "never"
			if d["connected"] == true {
				state = "connected"
			}
			if d["last_seen"] != nil {
				seen = line(d["last_seen"])
			}
			fmt.Printf("%-*s  %-16s  %-9s  last seen %s\n", width, line(d["id"]), line(d["name"]), state, seen)
		}
		return 0, true
	case "revoke":
		if len(args) != 2 {
			return 0, false
		}
		if ok, said := c.Write("device.revoke", map[string]any{"device": args[1]}); !ok {
			fmt.Fprintln(os.Stderr, "device.revoke:", said)
			return 1, true
		}
		fmt.Printf("device %s revoked; its connections are closed\n", args[1])
		return 0, true
	}
	return 0, false
}

func orEmpty(v any) any {
	if v == nil {
		return ""
	}
	return v
}

// cancel is `hl cancel M<n> [--yes]`: a request taken out of an agent's queue, after the daemon's plan is shown and confirmed.
func cancel(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, nil, []string{"--yes", "-y"}, map[string]string{"-y": "--yes"})
	if !ok || len(p.pos) != 1 {
		return 0, false
	}
	id, why, ok := requestArg(p.pos[0])
	if why != "" {
		fmt.Println(why)
		return 1, true
	}
	if !ok {
		return 0, false
	}
	if c.AskingAgent() != hlclient.Human {
		fmt.Println("cancel is the founder's: an agent takes back its own request with `hl withdraw`, and answers one it was handed with `hl say <them> --parent M<n>`.")
		return 1, true
	}
	if !p.flags["--yes"] {
		made, why, got := c.Written("request.cancel", map[string]any{"id": id, "dry_run": true})
		if !made {
			return said(false, why), true
		}
		if got["ok"] != true {
			return said(false, fmt.Sprintf("not cancelled - %v", got["plan"])), true
		}
		fmt.Println(got["plan"])
		if st, err := os.Stdin.Stat(); err != nil || st.Mode()&os.ModeCharDevice == 0 {
			fmt.Fprintln(os.Stderr, "refusing without a terminal to confirm in - pass --yes")
			return 1, true
		}
		fmt.Print("go ahead? [y/N] ")
		answer, _ := bufio.NewReader(os.Stdin).ReadString('\n')
		if a := strings.ToLower(strings.TrimSpace(answer)); a != "y" && a != "yes" {
			fmt.Println("left alone")
			return 1, true
		}
	}
	made, text, got := c.Written("request.cancel", map[string]any{"id": id})
	return said(made && got["ok"] == true, text), true
}

// teamSeal is `hl team seal`: the founder accepts team.yaml and the agent files as they are now.
func teamSeal(c *hlclient.Client) int {
	ok, text := c.Write("team.seal", nil)
	if !ok {
		fmt.Println(text)
		return 1
	}
	fmt.Println(text)
	return 0
}
