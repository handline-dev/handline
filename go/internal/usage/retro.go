package usage

import "github.com/handline/handline/go/internal/db"

// DoingWindow is a span (Lo, Hi] in which Agent held a task in doing.
type DoingWindow struct {
	Agent  string
	Lo, Hi int64
}

// WindowUse is the tokens and list-price cost of the calls Agent made inside one window, from its worktree.
type WindowUse struct {
	Tokens int64
	Cost   float64
}

// WindowUses is stats.retro's tokens: for each window, the calls in (Lo, Hi] whose directory is in the window's agent's worktree.
func WindowUses(q db.Q, ws []DoingWindow, table []priceEntry) ([]WindowUse, error) {
	trees, err := worktrees(q)
	if err != nil {
		return nil, err
	}
	out := make([]WindowUse, len(ws))
	for i, w := range ws {
		rows, err := q.Query("SELECT cwd, model, inp, cc, cr, out, thinking FROM token_calls WHERE ts > ? AND ts <= ? ORDER BY ts, rowid", w.Lo, w.Hi)
		if err != nil {
			return nil, err
		}
		for rows.Next() {
			var cwd, model string
			var t Tokens
			if err := rows.Scan(&cwd, &model, &t.Inp, &t.CC, &t.CR, &t.Out, &t.Thinking); err != nil {
				rows.Close()
				return nil, err
			}
			if agentForCwd(cwd, trees) == w.Agent {
				out[i].Tokens += t.Inp + t.CC + t.CR + t.Out
				out[i].Cost += Cost(model, t, table)
			}
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return nil, err
		}
	}
	return out, nil
}
