package hrwrites

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/adminwrites"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/sealed"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/timefmt"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// The appliers: what approving each kind of change does.

func (e *Env) roster() *team.Roster { return team.NewRoster(e.Home) }

func strPtr(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func (e *Env) roleOf(p step, with string) string {
	return fmt.Sprintf("Added on %s at %s's request%s: %s", stamp(p.get("created")), p.str("requested_by"), with, p.str("why"))
}

func (e *Env) register(name string, p step, repo, role string) error {
	_, err := e.roster().Register(name, team.RegisterOpts{Role: role, Repo: repo, Model: strPtr(p.str("model")), Effort: strPtr(p.str("effort")),
		Vendor: strPtr(p.str("vendor"))})
	return err
}

// ensureRepo is what the terminal host says of a repo: tmux knows a folder with a .git and otherwise says how to make it one.
func ensureRepo(path string) string {
	if strings.HasPrefix(path, "~") {
		if home, err := os.UserHomeDir(); err == nil {
			path = filepath.Join(home, path[1:])
		}
	}
	if r, err := filepath.EvalSymlinks(path); err == nil {
		path = r
	} else if a, err := filepath.Abs(path); err == nil {
		path = a
	}
	if _, err := os.Stat(filepath.Join(path, ".git")); err == nil {
		return path + " is a git repository"
	}
	if top := enclosingRepo(path); top != "" && top != path && trackedIn(top, path) {
		return fmt.Sprintf("%s is part of the git repository %s, not its top folder, so it cannot be a repo: set `repo:` to %s", path, top, top)
	}
	return path + " is not a git repository: run `git init` there"
}

func enclosingRepo(path string) string {
	here := path
	for {
		if _, err := os.Stat(filepath.Join(here, ".git")); err == nil {
			return here
		}
		up := filepath.Dir(here)
		if up == here {
			return ""
		}
		here = up
	}
}

func trackedIn(top, path string) bool {
	rel, err := filepath.Rel(top, path)
	if err != nil {
		return false
	}
	out, err := exec.Command("git", "-C", top, "ls-files", "--", rel).Output()
	return err == nil && strings.TrimSpace(string(out)) != ""
}

func registerRepo(repo string) string {
	if repo == "" {
		return ""
	}
	return " " + ensureRepo(repo) + "."
}

func (e *Env) doAdd(p step) (bool, string, error) {
	name := p.str("agent")
	// The table first: it is the write that can be refused, and a refusal after the folder exists leaves an agent nothing routes to.
	ok, said, err := e.editTeamYaml(func(t string) (string, error) { return addText(t, name, p.str("team")) }, name, "", nil)
	if err != nil || !ok {
		return false, said, err
	}
	if err := e.register(name, p, p.str("repo"), e.roleOf(p, "")); err != nil {
		return false, "", err
	}
	repo := p.str("repo")
	p.later(func() string { return registerRepo(repo) })
	teamWord := ""
	if t := p.str("team"); t != "" {
		teamWord = ", team " + t
	}
	return true, fmt.Sprintf("%s is an agent%s. %s", name, teamWord, said), nil
}

func (e *Env) doTeam(p step) (bool, string, error) {
	name, members := p.str("agent"), p.members()
	everyone := append([]string{name}, members...)
	edit := func(text string) (string, error) {
		for _, n := range everyone {
			var err error
			if text, err = addText(text, n, name); err != nil {
				return "", err
			}
		}
		return text, nil
	}
	verify := func(tm *team.Team) error {
		for _, n := range members {
			if err := need(tm.Route(name, n, 0, false).Allowed, fmt.Sprintf("%s -> %s is not routable", name, n)); err != nil {
				return err
			}
		}
		return nil
	}
	ok, said, err := e.editTeamYaml(edit, name, "", verify)
	if err != nil || !ok {
		return false, said, err
	}
	role := e.roleOf(p, ", with team "+name)
	seen := map[string]bool{}
	var repos []string
	for _, n := range everyone {
		repo := p.str("repo." + n)
		if n == name {
			repo = p.str("repo")
		}
		if err := e.register(n, p, repo, role); err != nil {
			return false, "", err
		}
		if repo != "" && !seen[repo] {
			seen[repo] = true
			repos = append(repos, repo)
		}
	}
	sortStrings(repos)
	for _, r := range repos {
		r := r
		p.later(func() string { return registerRepo(r) })
	}
	return true, fmt.Sprintf("team %s: %s are agents. %s", name, strings.Join(everyone, ", "), said), nil
}

func (e *Env) doRetire(p step) (bool, string, error) {
	name := p.str("agent")
	ok, said, err := e.editTeamYaml(func(t string) (string, error) { return removeText(t, name) }, "", name, nil)
	if err != nil || !ok {
		return false, said, err
	}
	// The scheduling row goes with it. Its requests and events stay - they are the record of work that was done.
	if _, err := p.tx.Exec("DELETE FROM agent_state WHERE agent = ?", name); err != nil {
		return false, "", err
	}
	src := paths.AgentDir(e.Home, name)
	dest := filepath.Join(e.Home, "agents", "archive", time.Now().Format("2006-01-02")+"-"+name)
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return false, "", err
	}
	if _, err := os.Stat(src); err == nil {
		if _, err := os.Stat(dest); err == nil {
			dest += "-" + p.id()
		}
		if err := os.Rename(src, dest); err != nil {
			return false, "", err
		}
	}
	if err := sealed.New(e.Home).Forget(sealed.AgentKey(name)); err != nil {
		return false, "", err
	}
	return true, fmt.Sprintf("%s is retired; its folder is at %s. %s", name, dest, said), nil
}

// edgesTouching is each edge this agent is an end of, as "<i>:<end>" - by position, so an edge is the same edge before and after the name in it changed.
func edgesTouching(t *team.Team, name string) map[string]bool {
	out := map[string]bool{}
	for i, ed := range t.Edges {
		if t.Matches(pyText(ed.From), name) {
			out[fmt.Sprintf("%d:from", i)] = true
		}
		if t.Matches(pyText(ed.To), name) {
			out[fmt.Sprintf("%d:to", i)] = true
		}
	}
	return out
}

// doRename makes one identity another, taking its whole history with it: its folder and its rows. Not while it is running.
func (e *Env) doRename(p step) (bool, string, error) {
	old, new := p.str("agent"), p.str("to")
	if e.Live == nil {
		return false, fmt.Sprintf("could not confirm whether %s has a session running just now - try again in a moment.", old), nil
	}
	live, ok := e.Live()
	if !ok {
		return false, fmt.Sprintf("could not confirm whether %s has a session running just now - try again in a moment.", old), nil
	}
	if live[old] {
		return false, fmt.Sprintf("%s has a session running. Close it first (`hl close %s`, or `c` on the screen) - an identity cannot change under a running session.", old, old), nil
	}
	src, dest := paths.AgentDir(e.Home, old), paths.AgentDir(e.Home, new)
	if _, err := os.Stat(dest); err == nil {
		return false, dest + " already exists", nil
	}
	before, err := team.LoadTeam(e.Home)
	if err != nil {
		return false, fmt.Sprintf("team.yaml cannot be read (%s); nothing renamed", err), nil
	}
	// Every edge that reached the old name must reach the new one: team.yaml is hand-written, and a block-style edge, or a glob like
	// `manager*`, is renamed by neither. Compare what the table actually routes, and refuse an edit that quietly dropped an edge.
	sameEdges := func(after *team.Team) error {
		was, now := edgesTouching(before, old), edgesTouching(after, new)
		var missing []string
		for k := range was {
			if !now[k] {
				missing = append(missing, k)
			}
		}
		if len(missing) > 0 {
			sortStrings(missing)
			if len(missing) > 3 {
				missing = missing[:3]
			}
			return configErr("%d edge(s) still name %s in a shape this cannot rewrite (%s) - rename it in team.yaml by hand first", len(edgesMissing(was, now)), old, strings.Join(missing, ", "))
		}
		return nil
	}
	ok, said2, err := e.editTeamYaml(func(t string) (string, error) { return renameText(t, old, new, p.str("team")) }, new, old, sameEdges)
	if err != nil || !ok {
		return false, said2, err
	}
	if _, err := os.Stat(src); err == nil {
		if err := os.Rename(src, dest); err != nil {
			return false, "", err
		}
	}
	r := e.roster()
	if b, err := os.ReadFile(r.AgentFile(new)); err == nil {
		pairs, isMap, perr := yamlcompat.ParsePairs(string(b))
		if perr != nil {
			return false, "", perr
		}
		if isMap {
			set := false
			for i := range pairs {
				if pairs[i].Key == "name" {
					pairs[i].Val, set = new, true
				}
			}
			if !set {
				pairs = append(pairs, yamlcompat.Pair{Key: "name", Val: new})
			}
			if err := r.Write(new, pairs); err != nil {
				return false, "", err
			}
			if err := sealed.New(e.Home).Forget(sealed.AgentKey(old)); err != nil {
				return false, "", err
			}
		}
	}
	for _, q := range []string{"UPDATE agent_state SET agent = ? WHERE agent = ?", "UPDATE requests SET requester = ? WHERE requester = ?",
		"UPDATE requests SET target = ? WHERE target = ?", "UPDATE events SET agent = ? WHERE agent = ?", "UPDATE handoff SET agent = ? WHERE agent = ?"} {
		if _, err := p.tx.Exec(q, new, old); err != nil {
			return false, "", err
		}
	}
	if _, err := p.tx.Exec("UPDATE cursors SET name = REPLACE(name, ?, ?) WHERE name LIKE ?", old, new, "%"+old+"%"); err != nil {
		return false, "", err
	}
	// The terminal host's worktree label is the host's to set; tmux keeps none.
	return true, fmt.Sprintf("%s is now %s: folder, team.yaml, and every row that named it. %s", old, new, said2), nil
}

func edgesMissing(was, now map[string]bool) []string {
	var out []string
	for k := range was {
		if !now[k] {
			out = append(out, k)
		}
	}
	return out
}

// ---------------------------------------------------------------- edges

var validResponse = []string{"async", "fire_and_forget"}

func pyStr(v any) string {
	if v == nil {
		return "None"
	}
	return fmt.Sprint(v)
}

// edgeRefusal is why this edge cannot be added or removed, or "" when it can. Read against the team.yaml as it is now; the write checks again,
// on the text it edits.
func (e *Env) edgeRefusal(kind, frm, to, response, relation string, registered []string, coming map[string]bool) string {
	if to == "" || !contains(registered, to) {
		shown := to
		if shown == "" {
			shown = "a target"
		}
		return shown + " is not an agent (an edge is between two agents)"
	}
	if to == frm {
		return "an agent cannot ask itself"
	}
	if response != "" && !contains(validResponse, response) {
		return "response must be one of " + strings.Join(validResponse, ", ")
	}
	if relation != "" && !relationRe.MatchString(relation) {
		return fmt.Sprintf("bad relation: %s (letters, digits, . _ - only)", pyRepr(relation))
	}
	t, err := team.LoadTeam(e.Home)
	if err != nil {
		return fmt.Sprintf("team.yaml cannot be read (%s)", err)
	}
	exact := false
	for _, ed := range t.Edges {
		exact = exact || (pyStr(ed.From) == frm && pyStr(ed.To) == to)
	}
	if kind == "link" {
		for _, end := range []string{frm, to} {
			if _, in := t.Agents[end]; !in && !coming[end] {
				return end + " is not in team.yaml agents - add it there first"
			}
		}
		if exact {
			return fmt.Sprintf("team.yaml already has the edge %s -> %s", frm, to)
		}
		for _, d := range t.Deny {
			if t.Matches(pyText(d.From), frm) && t.Matches(pyText(d.To), to) {
				reason := pyText(d.Reason)
				if reason == "" {
					reason = "no reason given"
				}
				return fmt.Sprintf("a deny in team.yaml covers %s -> %s (%s); an edge would never apply", frm, to, reason)
			}
		}
	} else if !exact {
		return fmt.Sprintf("team.yaml has no edge exactly %s -> %s to remove", frm, to)
	}
	return ""
}

// stillAllowedBy is the edge that lets frm ask to once the exact edge is gone, or "" when nothing does: a wildcard or team edge can route the
// same pair, so removing one edge is not always taking the permission away.
func stillAllowedBy(t *team.Team, frm, to string) string {
	for _, ed := range t.Edges {
		if pyStr(ed.From) == frm && pyStr(ed.To) == to {
			continue
		}
		if t.Matches(pyStr(ed.From), frm) && t.Matches(pyStr(ed.To), to) {
			if t.Route(frm, to, 0, false).Allowed {
				return fmt.Sprintf("%s -> %s", pyStr(ed.From), pyStr(ed.To))
			}
			return ""
		}
	}
	return ""
}

func unlinkCaveat(t *team.Team, frm, to string) string {
	if left := stillAllowedBy(t, frm, to); left != "" {
		return fmt.Sprintf(" %s can still ask %s: the edge %s allows it - this does not take the permission away.", frm, to, left)
	}
	return ""
}

func (e *Env) doLink(p step) (bool, string, error) {
	frm, to := p.str("agent"), p.str("to")
	if why := e.edgeRefusal("link", frm, to, p.str("response"), p.str("relation"), paths.ListAgents(e.Home), nil); why != "" {
		return false, why, nil
	}
	ok, said, err := e.editTeamYaml(func(t string) (string, error) { return e.linkText(t, frm, to, p.str("response"), p.str("relation")) }, "", "",
		func(tm *team.Team) error {
			return need(tm.Route(frm, to, 0, false).Allowed, fmt.Sprintf("%s -> %s is not routable", frm, to))
		})
	if err != nil || !ok {
		return false, said, err
	}
	return true, fmt.Sprintf("%s may now ask %s. %s", frm, to, said), nil
}

func (e *Env) doUnlink(p step) (bool, string, error) {
	frm, to := p.str("agent"), p.str("to")
	if why := e.edgeRefusal("unlink", frm, to, "", "", paths.ListAgents(e.Home), nil); why != "" {
		return false, why, nil
	}
	ok, said, err := e.editTeamYaml(func(t string) (string, error) { return unlinkText(t, frm, to) }, "", "", func(tm *team.Team) error {
		for _, ed := range tm.Edges {
			if pyStr(ed.From) == frm && pyStr(ed.To) == to {
				return configErr("the edge is still there")
			}
		}
		return nil
	})
	if err != nil || !ok {
		return false, said, err
	}
	t, err := team.LoadTeam(e.Home)
	if err != nil {
		return false, "", err
	}
	return true, fmt.Sprintf("the edge %s -> %s is removed.%s %s", frm, to, unlinkCaveat(t, frm, to), said), nil
}

// ---------------------------------------------------------------- settings, context, schedule, global

// idleClose is the idle close time of an agent, in the minutes the settings form takes.
type idleClose struct{ e *Env }

func parseIdle(text string) (int64, bool, error) {
	text = strings.TrimSpace(text)
	if text == "" {
		return 0, false, nil
	}
	n, err := strconv.ParseInt(text, 10, 64)
	if err != nil || n < 0 || strings.ContainsAny(text, "+- ") {
		return 0, false, fmt.Errorf("the idle close time is a whole number of minutes, 0 for never")
	}
	return n * 60, true, nil
}

func (idleClose) Parse(text string) error {
	_, _, err := parseIdle(text)
	return err
}

func (c idleClose) Set(text, agent string) (bool, string) {
	seconds, given, err := parseIdle(text)
	if err != nil {
		return false, err.Error()
	}
	var value any
	if given {
		value = seconds
	}
	if err := c.e.roster().SetKeys(agent, []yamlcompat.Pair{{Key: "park_timeout_s", Val: value}}); err != nil {
		return false, err.Error()
	}
	switch {
	case !given:
		return true, agent + ": idle close follows the global time"
	case seconds == 0:
		return true, "idle close never"
	}
	return true, fmt.Sprintf("idle close %d min", (seconds+59)/60)
}

func idleMinutes(seconds int64) string { return strconv.FormatInt((seconds+59)/60, 10) }

// doSettings writes exactly the settings that were proposed, through the register path.
func (e *Env) doSettings(p step) (bool, string, error) {
	agent := p.str("agent")
	changes, problem := e.roster().ChangesFor(e.DB, agent, p.sets(), "", idleMinutes)
	if len(changes) == 0 {
		if !strings.HasPrefix(problem, team.Nothing) {
			return false, problem, nil
		}
		return true, agent + "'s settings were already so", nil
	}
	ok, said := e.roster().SaveSettings(agent, changes, idleClose{e})
	return ok, said, nil
}

// SetByAgent is an agent's own change of its settings (the legacy agent_set): only its own, only the ordinary ones.
func (e *Env) SetByAgent(agent string, values map[string]string, speaker string) (bool, string) {
	return e.roster().SetByAgent(e.DB, agent, values, speaker, "", idleMinutes, idleClose{e})
}

var contextBudget = map[string]int{"ROLE.md": team.RoleBudgetBytes, "base.md": team.BaseBudgetBytes}

func contextProblem(texts [][2]string) string {
	if len(texts) == 0 {
		return "name ROLE.md or base.md, with the whole new text"
	}
	for _, t := range texts {
		fn, text := t[0], t[1]
		budget, known := contextBudget[fn]
		switch {
		case !known:
			return fmt.Sprintf("only ROLE.md and base.md can be changed this way, not %s", pyRepr(fn))
		case strings.TrimSpace(text) == "":
			return fn + " cannot be empty"
		case textsafe.HasUnsafe(strings.NewReplacer("\t", "", "\n", "").Replace(text)):
			return fn + " carries a control character"
		case len(text) > budget:
			return fmt.Sprintf("%s is %d bytes, over its %d-byte budget: it is paid for in every session", fn, len(text), budget)
		}
	}
	return ""
}

// doContext writes exactly the files that were proposed, as the founder's own change.
func (e *Env) doContext(p step) (bool, string, error) {
	var texts [][2]string
	for _, kv := range p.setPairs() {
		var text string
		if err := json.Unmarshal([]byte(kv[1]), &text); err != nil {
			return false, fmt.Sprintf("%s is not text", kv[0]), nil
		}
		texts = append(texts, [2]string{kv[0], text})
	}
	if problem := contextProblem(texts); problem != "" {
		return false, problem, nil
	}
	agent := p.str("agent")
	dir := paths.AgentDir(e.Home, agent)
	if st, err := os.Stat(dir); err != nil || !st.IsDir() {
		return false, agent + " is not an agent", nil
	}
	names := make([]string, len(texts))
	for i, t := range texts {
		if err := writeAtomic(filepath.Join(dir, t[0]), t[1]); err != nil {
			return false, "", err
		}
		names[i] = t[0]
	}
	return true, fmt.Sprintf("%s: %s replaced, from its next launch", agent, strings.Join(names, ", ")), nil
}

// doSchedule makes exactly the schedule change that was proposed.
func (e *Env) doSchedule(p step) (bool, string, error) {
	agent, st := p.str("agent"), p.sets()
	rd := e.reader()
	have, err := rd.LoadSchedules(agent)
	if err != nil {
		if bad, isBad := err.(*reads.ScheduleError); isBad {
			return false, bad.Msg, nil
		}
		return false, "", err
	}
	write := func(items []reads.Schedule) (bool, string, error) {
		next, err := reads.ParseSchedules(items)
		if err != nil {
			return false, err.Error(), nil
		}
		if err := adminwrites.WriteSchedules(e.Home, agent, next); err != nil {
			return false, "", err
		}
		return true, fmt.Sprintf("%s's schedules: %s done", agent, st["action"]), nil
	}
	if st["action"] == "add" {
		return write(append(have, reads.Schedule{Cron: st["cron"], Message: st["message"]}))
	}
	index := -1
	for i, it := range have {
		if reads.ScheduleSID(it) == st["sid"] {
			index = i
			break
		}
	}
	if index < 0 {
		return false, fmt.Sprintf("%s's schedule '%s' is no longer there as it was shown; nothing changed", agent, st["message_was"]), nil
	}
	if st["action"] == "edit" {
		have[index] = reads.Schedule{Cron: st["cron"], Message: st["message"]}
		return write(have)
	}
	rest := append(append([]reads.Schedule(nil), have[:index]...), have[index+1:]...)
	if len(rest) > 0 {
		if err := adminwrites.WriteSchedules(e.Home, agent, rest); err != nil {
			return false, "", err
		}
	} else if err := adminwrites.WriteSchedules(e.Home, agent, nil); err != nil {
		return false, "", err
	}
	return true, fmt.Sprintf("%s's schedules: %s done", agent, st["action"]), nil
}

// doGlobal sets one global switch.
func (e *Env) doGlobal(p step) (bool, string, error) {
	switchName, value := p.str("agent"), p.sets()["value"]
	var set func(get func(string) any, set func(string, any))
	switch switchName {
	case "idle-timeout":
		n := int64(0)
		if value != "never" {
			v, err := strconv.ParseInt(value, 10, 64)
			if err != nil || v < 0 || strings.ContainsAny(value, "+- ") {
				return false, switchName + " takes a number of seconds, or never", nil
			}
			n = v
		}
		set = func(_ func(string) any, put func(string, any)) { put("park_timeout_s", n) }
	case "notice-digest":
		n, err := timefmt.ParseSpan(value)
		if err != nil || n <= 0 {
			return false, switchName + " takes a positive span", nil
		}
		set = func(_ func(string) any, put func(string, any)) { put("notice_digest_s", n) }
	case "ignore-cache-expiry", "auto-accept", "dev-mode":
		if value != "on" && value != "off" {
			return false, switchName + " takes on or off", nil
		}
		on := value == "on"
		key := map[string]string{"ignore-cache-expiry": "ignore_cache_expiry", "auto-accept": "auto_accept_dialogs", "dev-mode": "dev_mode"}[switchName]
		set = func(_ func(string) any, put func(string, any)) {
			switch {
			case switchName == "ignore-cache-expiry" && !on:
				put(key, nil)
			default:
				put(key, on)
			}
		}
	default:
		return false, switchName + " is not a global switch", nil
	}
	if err := adminwrites.EditConfig(e.Home, set); err != nil {
		return false, "", err
	}
	return true, fmt.Sprintf("%s set to %s", switchName, value), nil
}
