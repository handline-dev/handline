package main

import (
	"fmt"
	"net/url"
	"os"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/textsafe"
)

func line(v any) string { return textsafe.Oneline(fmt.Sprint(v)) }

// The founder's own verbs left in hl, each for what no app can do yet.

// device is `hl device pair`: the code that pairs a first device when no paired app can make one. The daemon refuses it to an agent
// session. Listing and revoking devices are the app's.
func device(c *hlclient.Client, args []string) (int, bool) {
	if len(args) == 0 || args[0] != "pair" {
		return 0, false
	}
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
}

func orEmpty(v any) any {
	if v == nil {
		return ""
	}
	return v
}
