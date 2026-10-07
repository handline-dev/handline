package prompts

import (
	"database/sql"
	"fmt"

	"github.com/handline/handline/go/internal/term"
	"github.com/handline/handline/go/internal/textsafe"
)

// A dialog on an agent's terminal, answered by the founder from the bridge (handline/actions/dialogs.py): what the screen asks and
// offers (dialogOf), the pick pressed on it (pressChoice), and the standing answers the founder saved with "always": a trusted
// folder for the folder-trust screen, a dialog rule for any other dialog.
const (
	kindTrust  = "trust"
	trustLabel = "Trust this folder"
)

// handleOf is the terminal an agent is in, or "" when it has none or has closed it.
func (e *Env) handleOf(agent string) (string, error) {
	var terminal sql.NullString
	var closed sql.NullInt64
	err := e.DB.QueryRow("SELECT terminal, closed_at FROM agent_state WHERE agent = ?", agent).Scan(&terminal, &closed)
	if err == sql.ErrNoRows || (err == nil && (terminal.String == "" || (closed.Valid && closed.Int64 != 0))) {
		return "", nil
	}
	return terminal.String, err
}

// dialogInfo is what this agent's terminal is asking: kind `trust` for the folder-trust screen (one option: trust the folder
// shown) and `dialog` for a numbered dialog.
type dialogInfo struct {
	kind, title, vendor, folder string
	options                     []term.Option
}

func (e *Env) dialogOf(agent string) (*dialogInfo, error) {
	handle, err := e.handleOf(agent)
	if err != nil || handle == "" {
		return nil, err
	}
	vendor := e.vendorOf(agent)
	if folder := e.Term.TrustPromptFolder(handle); folder != "" {
		f := textsafe.Oneline(folder)
		return &dialogInfo{kind: kindTrust, title: "Trust " + f + "?", vendor: vendor, folder: f, options: []term.Option{{Number: 1, Label: trustLabel}}}, nil
	}
	d := e.Term.DialogOnScreen(handle)
	if d == nil {
		return nil, nil
	}
	if e.Term.TrustPromptShowing(handle) {
		// The trust screen with its folder unreadable is numbered like any dialog, but it is answered by the trust roots only:
		// offered here, "always" would save a rule nothing ever acts on.
		return nil, nil
	}
	return &dialogInfo{kind: "dialog", title: d.Title, vendor: vendor, options: d.Options}, nil
}

// pressChoice presses option number of the dialog titled title, after reading the screen again: what is pressed is what is on
// screen now, not what the modal showed. The caller holds the terminal lock. always saves the choice, remembered only once the press
// itself lands, never ahead of it: a rule saved before a press that then fails would stand for a choice nothing confirmed.
func (e *Env) pressChoice(agent, title string, number int, always bool) (bool, string) {
	now, err := e.dialogOf(agent)
	if err != nil {
		return false, fmt.Sprintf("%s: %v", agent, err)
	}
	if now == nil || now.title != title {
		return false, agent + ": the dialog is gone or changed - nothing pressed"
	}
	label, found := "", false
	for _, o := range now.options {
		if o.Number == number {
			label, found = o.Label, true
		}
	}
	if !found {
		return false, fmt.Sprintf("%s: the dialog has no option %d", agent, number)
	}
	handle, _ := e.handleOf(agent)
	var said string
	if now.kind == kindTrust {
		ok, s := e.Term.ConfirmTrust(handle)
		if !ok {
			return false, agent + ": " + s
		}
		said = s
	} else if outcome, _, why := e.Term.PressOption(handle, number); outcome != term.Cleared {
		if why == "" {
			why = outcome
		}
		return false, agent + ": " + why
	}
	if always {
		if ok, why := e.remember(now, label); !ok {
			return true, fmt.Sprintf("%s: chose %d. %s - but not remembered: %s", agent, number, label, why)
		}
	}
	if now.kind == kindTrust {
		return true, agent + ": " + said
	}
	out := fmt.Sprintf("%s: chose %d. %s", agent, number, label)
	if always {
		out += " - and will again"
	}
	return true, out
}

func (e *Env) remember(d *dialogInfo, label string) (bool, string) {
	if d.kind == kindTrust {
		if e.Trust.IsDeclared(d.folder) {
			return true, "already trusted"
		}
		return e.Trust.Enact(d.folder, "chosen from the agent's dialog")
	}
	if d.title == term.TruncatedTitle {
		// every long command shares this title, so a rule on it would answer them all
		return false, "a long command's title is not shown, so no rule can be kept for it"
	}
	return e.Rules.Add(d.vendor, d.title, label)
}
