package fold

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/atomicfile"
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/handoff"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/timefmt"
	"github.com/handline/handline/go/internal/usage"
)

// headlessModel is what a fold asks claude for: the cheap one.
const headlessModel = "haiku"

type event struct {
	ID, TS        int64
	Kind, Payload string
}

// notTold are the kinds the machine record leaves out one by one: turn boundaries and audit records.
var notTold = map[string]bool{"SessionStart": true, "SessionEnd": true, "Stop": true, "StopFailure": true, "SubagentStop": true,
	"PostCompact": true, "UserPromptSubmit": true, "verb_chain": true}

// idRE is an explicit reference to a request: #N, or "request N". A bare number is not one.
var idRE = regexp.MustCompile(`(?i)#(\d+)|\brequest\s+(\d+)\b`)

func (e *Env) eventsAfter(after int64, agent string) ([]event, error) {
	rows, err := e.DB.Query("SELECT id, ts, kind, payload FROM events WHERE id > ? AND agent = ? AND kind NOT IN ('fold','fold_failed') ORDER BY id LIMIT 10000", after, agent)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []event
	for rows.Next() {
		var ev event
		var p *string
		if err := rows.Scan(&ev.ID, &ev.TS, &ev.Kind, &p); err != nil {
			return nil, err
		}
		if p != nil {
			ev.Payload = *p
		}
		out = append(out, ev)
	}
	return out, rows.Err()
}

func facts(events []event) string {
	var lines []string
	if len(events) > 0 {
		lines = append(lines, fmt.Sprintf("- events folded: %d (ids %d..%d)", len(events), events[0].ID, events[len(events)-1].ID))
		count := map[string]int{}
		for _, ev := range events {
			count[ev.Kind]++
		}
		var kinds []string
		for k := range count {
			kinds = append(kinds, k)
		}
		sort.Strings(kinds)
		for i, k := range kinds {
			kinds[i] = fmt.Sprintf("%sx%d", k, count[k])
		}
		lines = append(lines, "- event kinds: "+strings.Join(kinds, ", "))
		var told []event
		for _, ev := range events {
			if !notTold[ev.Kind] {
				told = append(told, ev)
			}
		}
		if len(told) > 6 {
			told = told[len(told)-6:]
		}
		if len(told) > 0 {
			lines = append(lines, "- last events:")
			for _, ev := range told {
				p := []rune(ev.Payload)
				if len(p) > 90 {
					p = p[:90]
				}
				lines = append(lines, fmt.Sprintf("    %s %s %s", timefmt.Stamp(ev.TS, timefmt.Clock), ev.Kind, string(p)))
			}
		}
	} else {
		lines = append(lines, "- events folded: 0 (nothing new since the last fold)")
	}
	lines = append(lines, "- generated: "+time.Now().Format("2006-01-02 15:04:05"))
	return strings.Join(lines, "\n")
}

// reconcile moves an In progress or Blocked line to Done once every request it names is landed; a line naming none stays.
func (e *Env) reconcile(d *handoff.Digest) *handoff.Digest {
	for _, key := range []string{"in_progress", "blocked"} {
		var kept, resolved []string
		for _, item := range d.Items(key) {
			var ids []int64
			for _, m := range idRE.FindAllStringSubmatch(item, -1) {
				n, _ := strconv.ParseInt(m[1]+m[2], 10, 64)
				ids = append(ids, n)
			}
			all := len(ids) > 0
			for _, id := range ids {
				r, err := db.GetRequest(e.DB, id)
				all = all && err == nil && r != nil && r.State == "landed"
			}
			if all {
				resolved = append(resolved, item)
			} else {
				kept = append(kept, item)
			}
		}
		if len(resolved) > 0 {
			d.Sections[key] = kept
			d.Sections["done"] = append(append([]string{}, d.Items("done")...), resolved...)
		}
	}
	return d
}

func buildPrompt(agent string, previous *handoff.Digest, events []event, day string) string {
	prev := "(no previous digest)"
	if previous.Agent != "" {
		prev = previous.Render()
	}
	var delta []string
	for _, ev := range events {
		delta = append(delta, fmt.Sprintf("%s [%s] %s", timefmt.Stamp(ev.TS, timefmt.Clock), ev.Kind, ev.Payload))
	}
	through := previous.FoldedThrough
	if len(events) > 0 {
		through = int(events[len(events)-1].ID)
	}
	var heads []string
	for _, s := range handoff.Sections {
		heads = append(heads, "## "+s.Heading)
	}
	deltaText := "(none)"
	if len(delta) > 0 {
		deltaText = strings.Join(delta, "\n")
	}
	return fmt.Sprintf(`Rewrite the work digest below. Output ONLY the digest in markdown, nothing else.

Required format, with these exact section headings, all of them present:

# %s · %s
updated: %s · folded_through: event %d

%s

Rules:
- Every item already under Done in the previous digest MUST still appear under Done.
  Losing a completed item is the one unacceptable error.
- Move items between sections as the new events warrant; do not invent work.
- When an In progress or Blocked item is about a request you asked, write its id as
  #N (e.g. "waiting on #7") ONLY when that request is what the item is
  waiting on - reconcile_resolved closes the item the moment that request
  is done, so #N there means "done" ends this. A request being resolved is
  often when work on it starts, not when it finishes (e.g. a review coming
  back) - name that request another way instead ("auditor's review", "the
  C1 finding"), never as #N or "request N", or the item will close the
  moment the review lands, not when the work it starts is actually done.
- Keep each item one line, terse and concrete.
- Do not add sections beyond the ones listed.

=== previous digest ===
%s

=== new events since the last fold ===
%s
`, day, agent, time.Now().Format("15:04"), through, strings.Join(heads, "\n"), prev, deltaText)
}

// runModel is the one model call a fold is allowed: claude -p on the account.
func (e *Env) runModel(prompt, acct string) (bool, string) {
	exe, err := exec.LookPath("claude")
	if err != nil {
		return false, "claude not on PATH"
	}
	if !e.Accounts.LoggedIn(acct) {
		return false, fmt.Sprintf("account '%s' is not logged in", acct)
	}
	limit := e.Timeout
	if limit == 0 {
		limit = Timeout
	}
	ctx, cancel := context.WithTimeout(context.Background(), limit)
	defer cancel()
	cmd := exec.CommandContext(ctx, exe, "--model", headlessModel, "-p", prompt)
	for k, v := range e.Accounts.EnvFor(acct, "") {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	var stdout, stderr strings.Builder
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err = cmd.Run()
	switch {
	case ctx.Err() != nil:
		return false, fmt.Sprintf("fold timed out after %ds", int(limit.Seconds()))
	case err != nil && stderr.Len() == 0 && stdout.Len() == 0 && cmd.ProcessState == nil:
		return false, fmt.Sprintf("could not run claude: %v", err)
	case err != nil:
		msg := strings.TrimSpace(stderr.String())
		if msg == "" {
			msg = strings.TrimSpace(stdout.String())
		}
		if msg == "" {
			msg = "claude exited non-zero"
		}
		msg = textsafe.Oneline(msg)
		if r := []rune(msg); len(r) > 500 {
			msg = string(r[:500])
		}
		return false, msg
	}
	return true, strings.TrimSpace(stdout.String())
}

func stripFences(text string) string {
	t := strings.TrimSpace(text)
	if strings.HasPrefix(t, "```") {
		lines := strings.Split(t, "\n")[1:]
		if n := len(lines); n > 0 && strings.HasPrefix(strings.TrimSpace(lines[n-1]), "```") {
			lines = lines[:n-1]
		}
		t = strings.Join(lines, "\n")
	}
	return strings.TrimSpace(t)
}

// foldAccount is the claude account that pays for the call: one with a current reading and room, else the roomiest readable one
// under the wall. The fold is a spend nothing observes, so an account whose usage cannot be read is never used.
func (e *Env) foldAccount() string {
	var ours, readable []string
	for _, a := range paths.ListAccounts(e.Home) {
		if e.Usage.NotFor(a, team.DefaultVendor) == "" {
			ours = append(ours, a)
			if e.Accounts.LoggedIn(a) && !e.Usage.Read(a).Stale() {
				readable = append(readable, a)
			}
		}
	}
	if len(readable) > 0 {
		if picked, _, err := e.Usage.Pick(e.DB, usage.PickOpts{Candidates: readable, Vendor: team.DefaultVendor}); err == nil && picked != "" {
			return picked
		}
	}
	best, bestPct := "", 0
	for _, a := range ours {
		if !e.Accounts.LoggedIn(a) {
			continue
		}
		pct := e.Usage.Read(a).ShortTrusted()
		if pct == nil || *pct >= usage.ShortBlock {
			continue
		}
		if best == "" || *pct < bestPct {
			best, bestPct = a, *pct
		}
	}
	return best
}

func (e *Env) accountReasons() string {
	var out []string
	for _, a := range paths.ListAccounts(e.Home) {
		if !e.Accounts.LoggedIn(a) {
			out = append(out, a+" not logged in")
			continue
		}
		st := e.Usage.Read(a)
		switch pct := st.ShortTrusted(); {
		case pct == nil:
			_, why := st.Assignable(0)
			out = append(out, fmt.Sprintf("%s usage unreadable (%s)", a, why))
		case *pct >= usage.ShortBlock:
			out = append(out, fmt.Sprintf("%s at %d%%", a, *pct))
		default:
			out = append(out, fmt.Sprintf("%s at %d%% (usable)", a, *pct))
		}
	}
	if len(out) == 0 {
		return "no accounts registered"
	}
	return strings.Join(out, ", ")
}

func (e *Env) publish(path, text, message string) {
	if err := atomicfile.Write(path, text); err == nil {
		e.commit(path, message)
	}
}

// Fold is control.fold: the agent's digest rewritten from the events since its cursor. A rejected fold keeps the previous digest, puts a
// degraded one beside the facts and leaves the cursor, so the same events are folded next time. ok is false when it was rejected.
func (e *Env) Fold(agent string) (bool, string) {
	day := e.today()
	dir := filepath.Join(paths.AgentDir(e.Home, agent), "journal")
	_ = os.MkdirAll(dir, 0o755)
	jpath := filepath.Join(dir, day+".md")
	previous := handoff.ParseDigest("")
	if raw, err := os.ReadFile(jpath); err == nil {
		previous = handoff.ParseDigest(string(raw))
	}
	if previous.Agent == "" {
		earlier, _ := filepath.Glob(filepath.Join(dir, "*.md"))
		sort.Strings(earlier)
		if len(earlier) > 0 {
			if raw, err := os.ReadFile(earlier[len(earlier)-1]); err == nil {
				previous = handoff.ParseDigest(string(raw))
			}
		}
	}
	cursorName := "fold:" + agent
	cursor, err := db.Cursor(e.DB, cursorName)
	if err != nil {
		return false, fmt.Sprintf("%s: fold rejected - %v", agent, err)
	}
	events, err := e.eventsAfter(cursor, agent)
	if err != nil {
		return false, fmt.Sprintf("%s: fold rejected - %v", agent, err)
	}
	if len(events) == 0 {
		before := previous.Render()
		if after := e.reconcile(previous).Render(); after != before {
			e.publish(jpath, after, fmt.Sprintf("fold %s %s (reconcile)", agent, day))
		}
		return true, fmt.Sprintf("%s: nothing new to fold (cursor at %d)", agent, cursor)
	}
	top := events[len(events)-1].ID
	machine := facts(events)

	var candidate, reason, acct string
	if acct = e.foldAccount(); acct == "" {
		reason = "no account can pay for the fold call: " + e.accountReasons()
	} else if ok, out := e.runModel(buildPrompt(agent, previous, events, day), acct); ok {
		candidate = stripFences(out)
	} else {
		reason = out
	}
	valid, problems := false, []string{reason}
	if candidate != "" {
		var prev *handoff.Digest
		for _, s := range handoff.Sections {
			if len(previous.Items(s.Key)) > 0 {
				prev = previous
			}
		}
		valid, problems = handoff.Validate(candidate, prev, true)
	}
	model := any(nil)
	if candidate != "" {
		model = headlessModel
	}
	var acctV any
	if acct != "" {
		acctV = acct
	}
	if !valid {
		deg := handoff.ParseDigest("")
		deg.Date, deg.Agent, deg.FoldedThrough = day, agent, previous.FoldedThrough
		deg.Updated = time.Now().Format("15:04") + " (degraded, no model)"
		for _, s := range handoff.Sections {
			deg.Sections[s.Key] = append([]string{}, previous.Items(s.Key)...)
		}
		deg.Facts = machine
		deg.Late = []string{fmt.Sprintf("fold could not run; %d event(s) still pending", len(events))}
		e.publish(jpath, e.reconcile(handoff.ParseDigest(deg.Render())).Render(), fmt.Sprintf("fold %s %s (degraded)", agent, day))
		db.AddEvent(e.DB, agent, "fold_failed", db.KV{K: "problems", V: problems}, db.KV{K: "cursor", V: cursor},
			db.KV{K: "model", V: model}, db.KV{K: "account", V: acctV})
		why := strings.Join(problems, "; ")
		if r := []rune(why); len(r) > 300 {
			why = string(r[:300])
		}
		return false, fmt.Sprintf("%s: fold rejected, kept the previous digest and left the cursor at %d. Reason: %s", agent, cursor, why)
	}

	merged := e.reconcile(handoff.ParseDigest(candidate))
	merged.Facts, merged.Date, merged.Agent = machine, day, agent
	merged.Updated, merged.FoldedThrough = time.Now().Format("15:04"), int(top)
	e.publish(jpath, merged.Render(), fmt.Sprintf("fold %s %s", agent, day))
	err = e.InTx(func(q db.Q) error {
		if err := db.SetCursor(q, cursorName, top); err != nil {
			return err
		}
		_, err := db.AddEvent(q, agent, "fold", db.KV{K: "through", V: top}, db.KV{K: "events", V: len(events)},
			db.KV{K: "model", V: model}, db.KV{K: "account", V: acctV})
		return err
	})
	if err != nil {
		return false, fmt.Sprintf("%s: fold rejected - %v", agent, err)
	}
	return true, fmt.Sprintf("%s: folded %d events through %d", agent, len(events), top)
}
