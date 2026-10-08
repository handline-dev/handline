package taskwrites

import (
	"database/sql"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/textsafe"
)

// questionBody is actions.question_body: what an agent is told when the founder asks about a task.
func questionBody(tid int64, text string) string {
	return fmt.Sprintf("Question on T%d from the founder (the task keeps its holder): %s\n\n"+
		"Answer by replying to this message: hl say human --parent <this message's #id> '<answer>'", tid, text)
}

// askMethod is task.ask (actions.ask_on_task): the founder asks an agent about a task from its page. The question is a say linked to the task,
// whose holder does not change; the agent's reply joins the task. The message, its link, the task_question event and the record of the result
// are one transaction.
func askMethod(home string, tell Teller) func(*rpc.Conn, map[string]any) (any, error) {
	return func(c *rpc.Conn, p map[string]any) (any, error) {
		tid, ok := rpc.AsInt(p["task"])
		if !ok {
			return nil, badParam("task is a task number", "task")
		}
		to, ok := p["to"].(string)
		if !ok || to == "" {
			return nil, badParam("to is an agent's name", "to")
		}
		question, ok := p["text"].(string)
		if !ok || strings.TrimSpace(question) == "" {
			return nil, badParam("text is the question, not empty", "text")
		}
		body := questionBody(tid, strings.TrimSpace(textsafe.Text(question)))
		var asked *Asked
		out, err := c.Effect(func(tx *sql.Tx) (map[string]any, error) {
			asked = nil
			if has, err := hasTask(tx, tid); err != nil {
				return nil, err
			} else if !has {
				e := rpc.NotFoundError(fmt.Sprintf("no task T%d", tid))
				e.Data = map[string]any{"what": "task", "id": tid}
				return nil, e
			}
			if !contains(paths.ListAgents(home), to) {
				e := rpc.NotFoundError("no such agent: " + to)
				e.Data = map[string]any{"what": "agent", "name": to}
				return nil, e
			}
			var err error
			if tell == nil {
				return nil, fmt.Errorf("task.ask needs the supervisor to route its say")
			}
			asked, err = tell.TaskSay(tx, to, body, func(rid int64, _ string) error {
				if err := db.LinkRequest(tx, tid, rid); err != nil {
					return err
				}
				_, err := db.AddEventDeduped(tx, human, "task_question", fmt.Sprintf("task_question:%d", rid), db.KV{K: "task", V: tid}, db.KV{K: "request", V: rid})
				return err
			})
			if err != nil {
				return nil, err
			}
			if !asked.OK {
				return nil, &rpc.CommitThen{Err: rpc.Refused(asked.Msg, "", "")}
			}
			return map[string]any{"accepted": true, "said": asked.Msg, "request": asked.RID}, nil
		})
		if asked != nil && !asked.OK && asked.After != nil {
			asked.After() // the refusal's writes stood: what follows them still runs
		}
		if err != nil {
			return nil, err
		}
		if asked.After != nil {
			if extra := asked.After(); extra != "" {
				out["said"] = asked.Msg + "\n" + extra
			}
		}
		return out, nil
	}
}
