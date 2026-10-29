package prompts

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/term"
)

// recordAnswer keeps what prompt.answer answered (prompt_answers), so the chat's prompt item says it once the prompt closes: the
// choice as the caller sent it, the words for it, the typed text or a deny's reason as the note, and who answered.
func (e *Env) recordAnswer(c *rpc.Conn, b *Block, choice any, pick Choice) error {
	raw, err := json.Marshal(choice)
	if err != nil {
		return err
	}
	label, note := answerLabel(b, choice, pick)
	by, _ := c.Caller.Info["name"].(string)
	var noteVal any
	if note != "" {
		noteVal = note
	}
	_, err = e.DB.Exec("INSERT OR REPLACE INTO prompt_answers(prompt_id, agent, kind, choice, label, note, by_name, at) VALUES(?,?,?,?,?,?,?,?)",
		b.ID, b.Agent, b.Kind, string(raw), label, noteVal, by, e.now().Unix())
	return err
}

// answerLabel is the words for an answer as the chat line shows them: Allowed or Denied for a permission, the picked options' labels
// for a question (Other for typed text, which is the note), the option's label for a dialog, Cancelled for Esc.
func answerLabel(b *Block, choice any, pick Choice) (label, note string) {
	if pick.Cancel {
		if b.Kind == term.PromptPermission {
			return "Denied", ""
		}
		return "Cancelled", ""
	}
	switch b.Kind {
	case term.PromptPermission:
		if pick.Yes {
			return "Allowed", ""
		}
		return "Denied", pick.Note
	case term.PromptQuestion:
		opts, _ := b.Detail["options"].([]any)
		labelOf := func(i int) string {
			if i >= 0 && i < len(opts) {
				if o, _ := opts[i].(map[string]any); o != nil {
					if l, _ := o["label"].(string); l != "" {
						return l
					}
				}
			}
			return fmt.Sprint(i + 1)
		}
		if pick.Text != nil {
			return "Other", *pick.Text // the Other row is not among the options
		}
		var labels []string
		for _, i := range pick.Choices {
			labels = append(labels, labelOf(i))
		}
		return strings.Join(labels, ", "), ""
	}
	opts, _ := b.Detail["options"].([]any)
	for _, o := range opts {
		if pair, _ := o.([]any); len(pair) >= 2 && fmt.Sprint(pair[0]) == fmt.Sprint(choice) {
			return fmt.Sprint(pair[1]), ""
		}
	}
	return fmt.Sprint(choice), ""
}
