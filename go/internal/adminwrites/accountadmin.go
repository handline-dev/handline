package adminwrites

import (
	"context"
	"errors"
	"fmt"
	"os"
	osexec "os/exec"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
)

// account.add and account.remove (handline/team/accounts.py add_account, remove_account): the account's directory made and linked to the
// shared files with nothing logged in, and the sequence of `hl account remove`. The login is account.login's; trusting the agents'
// worktrees on the new account is settings.trust's.

func (e *Env) accountAdd(c *rpc.Conn, p map[string]any) (any, error) {
	acct, ok := p["account"].(string)
	if !ok || acct == "" {
		return nil, badParam("account is the new account's name", "account")
	}
	vendor := ""
	if v := p["vendor"]; v != nil {
		if vendor, ok = v.(string); !ok || vendor == "" {
			return nil, badParam("vendor is the tool the account is for", "vendor")
		}
	}
	_, err := team.Accounts{Home: e.Home}.Add(acct, vendor)
	var unknown *team.UnknownVendor
	switch {
	case errors.Is(err, team.ErrBadAccountName):
		return nil, badParam(err.Error(), "account")
	case errors.As(err, &unknown):
		return nil, badParam(err.Error(), "vendor")
	case errors.Is(err, team.ErrAccountExists):
		return nil, refused(err.Error(), "state")
	case err != nil:
		return nil, refused(err.Error(), "")
	}
	return wrote(c, fmt.Sprintf("registered account '%s'; log it in with: hl login %s", acct, acct), map[string]any{"account": acct})
}

func (e *Env) accountRemove(c *rpc.Conn, p map[string]any) (any, error) {
	acct, err := e.accountNamed(p)
	if err != nil {
		return nil, err
	}
	dry, _, err := flagParam(p, "dry_run")
	if err != nil {
		return nil, err
	}
	confirm := ""
	if v := p["confirm"]; v != nil {
		s, ok := v.(string)
		if !ok {
			return nil, badParam("confirm is the account's name", "confirm")
		}
		confirm = s
	}
	accts := team.Accounts{Home: e.Home}
	if dry {
		would, lines, err := accts.RemovePreview(e.DB, acct, time.Now())
		if err != nil {
			return nil, err
		}
		return map[string]any{"accepted": true, "said": strings.Join(lines, "\n"), "would_remove": would, "plan": lines}, nil
	}
	if strings.TrimSpace(confirm) != acct {
		return nil, badParam(fmt.Sprintf("confirm is the account's name, %q, as the founder typed it", acct), "confirm")
	}
	run := e.Run
	if run == nil {
		run = logout
	}
	said, err := accts.Remove(e.DB, acct, time.Now(), run)
	var no *team.RemoveRefused
	if errors.As(err, &no) {
		return nil, refused(no.Why, "state")
	}
	if err != nil {
		return nil, err
	}
	if rid, ok := p["request_id"].(string); ok {
		if err := c.RecordWrite("account.remove", rid); err != nil {
			return nil, err
		}
	}
	return map[string]any{"accepted": true, "said": said}, nil
}

// logout runs the vendor's logout command with the account's environment. The vendor removes its own credential.
func logout(argv []string, env map[string]string) int {
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	cmd := osexec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Env = os.Environ()
	for k, v := range env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	if err := cmd.Run(); err != nil {
		var exit *osexec.ExitError
		if errors.As(err, &exit) {
			return exit.ExitCode()
		}
		return -1
	}
	return 0
}
