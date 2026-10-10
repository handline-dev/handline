package main

import (
	"fmt"
	"os"

	"github.com/handline/handline/go/internal/hlclient"
)

// account is the `hl account` subcommands ported so far: cap. The rest print what accounts.list does not carry yet.
func account(c *hlclient.Client, args []string) (int, bool) {
	if len(args) == 0 || args[0] != "cap" {
		return 0, false
	}
	return accountCap(c, args[1:])
}

// accountCap shows or sets the most agents mid-turn on an account (cli/account.py cmd_account_cap): the cap in force from
// accounts.list, set through account.set. Setting is the founder's; an account the daemon does not list, or a cap below
// one, Python answers in its own words.
func accountCap(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, nil, nil, nil)
	if !ok || len(p.pos) < 1 || len(p.pos) > 2 {
		return 0, false
	}
	name, n, set := p.pos[0], 0, len(p.pos) == 2
	if set {
		if n, ok = integer(p.pos[1]); !ok || n < 1 || !settingsCaller(c) {
			return 0, false
		}
	}
	got, err := c.Call("accounts.list", nil)
	if err != nil {
		return 0, false
	}
	list, _ := got["accounts"].([]any)
	var acct map[string]any
	for _, a := range list {
		if m, _ := a.(map[string]any); m != nil && m["name"] == name {
			acct = m
		}
	}
	capNow, known := acct["cap_effective"].(float64)
	if !known {
		return 0, false
	}
	c.NoteVerbChain([]string{"account", "cap"})
	if !set {
		fmt.Printf("%s: at most %d agents mid-turn at once\n", name, int(capNow))
		return 0, true
	}
	if ok, said := c.Write("account.set", map[string]any{"account": name, "cap": n}); !ok {
		fmt.Fprintln(os.Stderr, said)
		return 1, true
	}
	fmt.Printf("%s: at most %d agents mid-turn at once\n", name, n)
	return 0, true
}
