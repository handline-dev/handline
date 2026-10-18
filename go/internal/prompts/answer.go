package prompts

import (
	"fmt"
	"time"

	"github.com/handline/handline/go/internal/hooks"
	"github.com/handline/handline/go/internal/lock"
	"github.com/handline/handline/go/internal/term"
)

// Choice is what Answer presses: permission {Yes}; question {Choices (0-based), Text}; dialog {Number, Always}; any kind {Cancel},
// which sends Esc (a permission prompt reads it as No).
type Choice struct {
	Cancel  bool
	Yes     bool
	Choices []int
	Text    *string
	Number  int
	Always  bool
}

// Answer answers block blockID with choice, locking the terminal the tick types into. Refused, and recorded as refused, unless
// the screen read again now shows the very prompt the row names: same kind, same panel text and, for a permission prompt, the hook
// still waiting on the same wait id.
func (e *Env) Answer(blockID int64, c Choice) (bool, string) {
	row, err := e.get(blockID)
	if err != nil {
		return false, err.Error()
	}
	if row == nil {
		return false, fmt.Sprintf("no prompt #%d", blockID)
	}
	agent := row.Agent
	if row.GoneAt.Valid {
		return e.record(row, false, agent+": the prompt is gone - nothing sent")
	}
	if !row.Answerable {
		return e.record(row, false, agent+": this prompt has no hook record to pin it to - answer it in its terminal")
	}
	hold := lock.TakeTerminal(e.Home, agent)
	if hold == nil {
		return false, agent + ": something else is using its terminal; try again"
	}
	defer hold.Release()
	handle, err := e.handleOf(agent)
	if err != nil {
		return false, err.Error()
	}
	if handle == "" {
		return e.record(row, false, agent+": it has no terminal to answer in")
	}
	kind, conf, q, d := e.Term.PromptOnScreen(handle)
	if kind == term.PromptNone || kind == term.PromptUnreadable {
		return e.record(row, false, agent+": the prompt is gone or its screen cannot be read - nothing sent")
	}
	now := e.describe(agent, kind, conf, q, d, hooks.ReadWait(e.Home, agent))
	if now.Kind != row.Kind || Fingerprint(now.Kind, now.Panel) != row.Fingerprint {
		return e.record(row, false, agent+": the prompt on screen is not the one shown - nothing sent")
	}
	var ok bool
	var said string
	switch {
	case c.Cancel:
		ok, said = e.cancel(agent, handle, row)
	case row.Kind == term.PromptPermission:
		ok, said = e.permission(agent, handle, row, now, c.Yes)
	case row.Kind == term.PromptQuestion:
		ok, said = e.question(agent, handle, now, c.Choices, c.Text)
	default:
		ok, said = e.pressChoice(agent, now.Title, c.Number, c.Always)
	}
	return e.record(row, ok, said)
}

// cancel is Esc, once the prompt was just confirmed to be the row's. A permission prompt or question reads Esc as No or cancel. A
// plain dialog gets it only where DecliningKey says the screen shows Esc only declines, not one whose Esc leaves the session.
func (e *Env) cancel(agent, handle string, row *Block) (bool, string) {
	if row.Kind == term.PromptDialog {
		outcome, _, why := e.Term.DeclineDialog(handle)
		if outcome == term.Cleared {
			return true, agent + ": cancelled (Esc)"
		}
		if why == "" {
			why = outcome
		}
		return false, agent + ": not cancelled - " + why
	}
	if ok, out := e.Term.Send(handle, term.Escape, false); !ok {
		return false, fmt.Sprintf("%s: the key could not be sent: %s", agent, cutTo(out, 120))
	}
	for i := 0; i < 4; i++ {
		time.Sleep(e.Term.Settle)
		kind, conf, q, d := e.Term.PromptOnScreen(handle)
		if kind == term.PromptUnreadable {
			continue // not evidence either way
		}
		if kind == term.PromptNone || Fingerprint(kind, e.describe(agent, kind, conf, q, d, map[string]any{}).Panel) != row.Fingerprint {
			if row.Kind == term.PromptPermission {
				return true, agent + ": answered No (Esc)"
			}
			return true, agent + ": cancelled (Esc)"
		}
	}
	return false, agent + ": the prompt is still on screen after Esc"
}

func cutTo(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

func (e *Env) permission(agent, handle string, row *Block, now Seen, yes bool) (bool, string) {
	same := func() bool {
		id, has := hooks.WaitID(hooks.ReadWait(e.Home, agent))
		return has && row.WaitID.Valid && id == row.WaitID.Int64
	}
	outcome, _, why := e.Term.AnswerConfirmation(handle, yes, now.Panel, same)
	if outcome == term.Cleared {
		if yes {
			return true, agent + ": answered Yes"
		}
		return true, agent + ": answered No"
	}
	if why == "" {
		why = outcome
	}
	return false, agent + ": not answered - " + why
}

func (e *Env) question(agent, handle string, now Seen, choices []int, text *string) (bool, string) {
	outcome, _, why := e.Term.AnswerQuestion(handle, now.Panel, choices, text)
	if outcome == term.Cleared {
		if why != "" {
			return true, agent + ": answered (" + why + ")"
		}
		return true, agent + ": answered"
	}
	if why == "" {
		why = outcome
	}
	return false, agent + ": not answered - " + why
}

func (e *Env) record(row *Block, ok bool, said string) (bool, string) {
	okInt := 0
	if ok {
		okInt = 1
	}
	e.DB.Exec("UPDATE prompt_blocks SET answered_at = ?, answer_ok = ?, answer_said = ? WHERE id = ?", e.now().Unix(), okInt, said, row.ID)
	return ok, said
}
