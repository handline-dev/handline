// Package adminwrites serves the founder's configuration writes (handline/daemon/rpc_schedules.py, rpc_settings.py, rpc_accounts.py,
// rpc_team.py): schedules, settings, an account's cap and alias. They write files, so an effect and its write_effects
// record cannot be one transaction: each writes what a retry writes again and records its result once the write is done.
package adminwrites

import (
	"database/sql"
	"os"
	"strings"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/weblogin"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// Env is what the writes run on.
type Env struct {
	DB   *sql.DB
	Home string
	// Logins runs the web logins; nil serves neither account.login nor account.login_cancel.
	Logins *weblogin.Manager
	// Run runs the vendor's logout for account.remove and answers its exit code (-1 when it could not run); nil runs the command.
	Run func(argv []string, env map[string]string) int
	// Changed tells the accounts topic that an account was added; nil tells no one.
	Changed func(account string)
}

// Register adds the writes to the server's table.
func Register(s *rpc.Server, e *Env) {
	s.Register("schedule.add", rpc.Method{Role: "founder", Write: true, Handler: e.scheduleAdd})
	s.Register("schedule.edit", rpc.Method{Role: "founder", Write: true, Handler: e.scheduleEdit})
	s.Register("schedule.remove", rpc.Method{Role: "founder", Write: true, Handler: e.scheduleRemove})
	s.Register("worktree.set_status", rpc.Method{Role: "member", Write: true, Handler: e.worktreeSetStatus})
	s.Register("settings.set", rpc.Method{Role: "founder", Write: true, Handler: e.settingsSet})
	s.Register("account.set", rpc.Method{Role: "founder", Write: true, Handler: e.accountSet})
	s.Register("account.add", rpc.Method{Role: "founder", Write: true, Handler: e.accountAdd})
	// Unkept and OwnEvent: a dry run writes and records nothing, and a removal that happened is judged again by a retry (no such account).
	s.Register("account.remove", rpc.Method{Role: "founder", Write: true, Unkept: true, OwnEvent: true, Handler: e.accountRemove})
	if e.Logins != nil {
		s.Register("account.login", rpc.Method{Role: "founder", Write: true, Handler: e.accountLogin, Refresh: e.refreshLogin})
		s.Register("account.login_cancel", rpc.Method{Role: "founder", Write: true, Handler: e.accountLoginCancel})
		s.Register("account.login_code", rpc.Method{Role: "founder", Write: true, Unkept: true, Handler: e.accountLoginCode})
	}
}

func badParam(message, field string) *rpc.Error {
	return rpc.BadRequestError(message, field, rpc.InvalidParams)
}

func notFound(what, name, message string) *rpc.Error {
	e := rpc.NotFoundError(message)
	e.Data = map[string]any{"what": what, "name": name}
	return e
}

func refused(msg, gate string) *rpc.Error { return rpc.Refused(msg, gate, "") }

// text is a string that is not blank; when it is optional and absent, ok is false.
func text(p map[string]any, key string, required bool) (s string, ok bool, err error) {
	v := p[key]
	if v == nil && !required {
		return "", false, nil
	}
	s, isStr := v.(string)
	if !isStr || strings.TrimSpace(s) == "" {
		return "", false, badParam(key+" is text and not empty", key)
	}
	return s, true, nil
}

func (e *Env) agentNamed(p map[string]any, key string) (string, error) {
	name, ok := p[key].(string)
	if !ok || name == "" {
		return "", badParam(key+" is an agent's name", key)
	}
	for _, a := range paths.ListAgents(e.Home) {
		if a == name {
			return name, nil
		}
	}
	return "", notFound("agent", name, "no such agent: "+name)
}

func (e *Env) accountNamed(p map[string]any) (string, error) {
	name, ok := p["account"].(string)
	if !ok || name == "" {
		return "", badParam("account is a name", "account")
	}
	for _, a := range paths.ListAccounts(e.Home) {
		if a == name {
			return name, nil
		}
	}
	return "", notFound("account", name, "no such account: "+name)
}

// wrote records the result of a write that is done, and answers it.
func wrote(c *rpc.Conn, said string, more map[string]any) (any, error) {
	return c.Effect(func(*sql.Tx) (map[string]any, error) {
		out := map[string]any{"accepted": true, "said": said}
		for k, v := range more {
			out[k] = v
		}
		return out, nil
	})
}

// config is config.yaml as a write keeps it: every other setting stays. A missing or empty file is empty; one that is not a mapping is not
// overwritten.
type config struct {
	path  string
	pairs []yamlcompat.Pair
}

func (e *Env) readConfig() (*config, error) {
	path := paths.ConfigFile(e.Home)
	b, err := os.ReadFile(path)
	if os.IsNotExist(err) || (err == nil && strings.TrimSpace(string(b)) == "") {
		return &config{path: path}, nil
	}
	if err != nil {
		return nil, err
	}
	pairs, isMap, err := yamlcompat.ParsePairs(string(b))
	if err != nil {
		return nil, err
	}
	if !isMap {
		return nil, &yamlcompat.ConfigError{Msg: path + " is not a mapping; not overwriting it"}
	}
	return &config{path, pairs}, nil
}

func (c *config) get(k string) any {
	for _, p := range c.pairs {
		if p.Key == k {
			return p.Val
		}
	}
	return nil
}

func (c *config) set(k string, v any) {
	if v == nil {
		for i, p := range c.pairs {
			if p.Key == k {
				c.pairs = append(c.pairs[:i:i], c.pairs[i+1:]...)
				return
			}
		}
		return
	}
	for i, p := range c.pairs {
		if p.Key == k {
			c.pairs[i].Val = v
			return
		}
	}
	c.pairs = append(c.pairs, yamlcompat.Pair{Key: k, Val: v})
}

// save replaces config.yaml in one step, so a reader sees the old file or the new.
func (c *config) save() error {
	out, err := yamlcompat.Dump(c.pairs)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dirOf(c.path), ".config.yaml.*.tmp")
	if err != nil {
		return err
	}
	_, werr := tmp.WriteString(out)
	if werr == nil {
		werr = tmp.Chmod(0o644)
	}
	if cerr := tmp.Close(); werr == nil {
		werr = cerr
	}
	if werr != nil {
		os.Remove(tmp.Name())
		return werr
	}
	if err := os.Rename(tmp.Name(), c.path); err != nil {
		os.Remove(tmp.Name())
		return err
	}
	return nil
}

func dirOf(path string) string {
	if i := strings.LastIndexByte(path, '/'); i >= 0 {
		return path[:i]
	}
	return "."
}

// EditConfig reads config.yaml, lets edit change it (a nil value removes a key) and writes it in one step; a file that is not a mapping is
// not overwritten.
func EditConfig(home string, edit func(get func(string) any, set func(string, any))) error {
	cfg, err := (&Env{Home: home}).readConfig()
	if err != nil {
		return err
	}
	edit(cfg.get, cfg.set)
	return cfg.save()
}
