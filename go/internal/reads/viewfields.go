package reads

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/team"
)

// Wall is what holds back a wake that is due: the account that would wake the agent.
type Wall struct {
	Account string
	// Clock is when the account's window rolls over, unix seconds; 0 when it is not known or the wait is for a slot, not a window.
	Clock   int64
	Assumed bool
}

// viewFields are the fields of an agent's view that are read from the board, the team files and the lifecycle, not stored with it
// (AgentView kind, task_count, ready, wall, fire, attention, cache_ignored, config_error). One builder for agents.list, agent.get and the working line.
type viewFields struct {
	taskCount map[string]int
	// ready is the agents whose current task is queued and held by no open blocker.
	ready map[string]bool
	// lastToFounder is each agent's newest request to the founder, whatever became of it; founderUnread counts those the founder has
	// not yet seen (past the for_you_seen cursor, as `hl inbox` counts them).
	lastToFounder map[string]map[string]any
	founderUnread map[string]int
}

// viewFields reads the board once for every agent.
func (e *Env) viewFields() (*viewFields, error) {
	open, err := e.openTasks()
	if err != nil {
		return nil, err
	}
	b, err := e.loadBoard()
	if err != nil {
		return nil, err
	}
	blocked := map[int64]bool{}
	rows, err := query(e.DB, "SELECT DISTINCT b.task_id FROM task_blocks b JOIN tasks t ON t.id = b.blocks_on "+
		"WHERE b.dormant = 0 AND t.discarded_by IS NULL AND t.done_evidence IS NULL")
	if err != nil {
		return nil, err
	}
	for _, r := range rows {
		blocked[r.i("task_id")] = true
	}
	f := &viewFields{taskCount: map[string]int{}, ready: map[string]bool{}, lastToFounder: map[string]map[string]any{},
		founderUnread: map[string]int{}}
	if err := e.founderFields(f); err != nil {
		return nil, err
	}
	for _, t := range open {
		owner, id := t.str("owner"), t.i("id")
		state := b.state(t)
		// Same rule as open_task_counts: a draft counts only as the task its owner holds.
		if id == b.current[owner] || state != "draft" {
			f.taskCount[owner]++
		}
		if id == b.current[owner] && state == "queued" && !blocked[id] {
			f.ready[owner] = true
		}
	}
	return f, nil
}

// founderFields reads what each agent last said to the founder and how much of its mailbox they have not seen.
func (e *Env) founderFields(f *viewFields) error {
	last, err := query(e.DB, "SELECT requester, ts, body FROM requests WHERE target = ? AND id IN "+
		"(SELECT MAX(id) FROM requests WHERE target = ? GROUP BY requester)", human, human)
	if err != nil {
		return err
	}
	for _, r := range last {
		f.lastToFounder[r.str("requester")] = map[string]any{"text": r.str("body"), "at": ts(r.i("ts"))}
	}
	rows, err := e.mailboxRows()
	if err != nil {
		return err
	}
	seen := map[string]int64{}
	for _, m := range rows {
		cur, ok := seen[m.from]
		if !ok {
			cr, err := queryOne(e.DB, "SELECT row_id FROM cursors WHERE name = ?", "for_you_seen:"+m.from)
			if err != nil {
				return err
			}
			cur = cr.i("row_id")
			seen[m.from] = cur
		}
		if m.id > cur {
			f.founderUnread[m.from]++
		}
	}
	return nil
}

// viewCtx is what the fields of every agent's view are read from, read once.
type viewCtx struct {
	*viewFields
	roster *team.Roster
}

func (e *Env) viewCtx() (*viewCtx, error) {
	f, err := e.viewFields()
	return &viewCtx{f, team.NewRoster(e.Home)}, err
}

// addFields puts the fields on a decoded view.
func (e *Env) addFields(name string, view map[string]any, x *viewCtx) error {
	cfg, cfgErr := x.roster.Read(name)
	view["config_error"] = nil
	if cfgErr != nil {
		view["config_error"] = oneline(cfgErr.Error())
	} else {
		view["kind"] = team.Kind(cfg)
	}
	view["cache_ignored"] = (&lifecycle.Settings{Home: e.Home, Roster: x.roster}).IgnoreCacheExpiry(name)
	view["task_count"] = x.taskCount[name]
	view["last_to_founder"], view["founder_unread"] = nil, x.founderUnread[name]
	if m := x.lastToFounder[name]; m != nil {
		view["last_to_founder"] = m
	}
	view["ready"] = x.ready[name] && screenIdle(view["state"])
	if e.Delivery == nil {
		// No lifecycle here to say: the wall, fire and attention the stored view holds (the Python tick writes them) stand.
		for _, k := range []string{"wall", "fire", "attention"} {
			if _, has := view[k]; !has {
				view[k] = nil
			}
		}
		return nil
	}
	view["wall"], view["fire"] = nil, nil
	row, err := db.GetAgentState(e.DB, name)
	if err != nil {
		return err
	}
	if row != nil {
		if err := e.addWall(name, view, row); err != nil {
			return err
		}
		if account, _ := view["account"].(string); account != "" && cfgErr == nil && len(cfg) > 0 {
			if t, err := e.Delivery.Trigger(e.DB, name, row, account); err != nil {
				return err
			} else if t.Fired {
				view["fire"] = t.Kind
			}
		}
	}
	view["attention"], err = e.attention(name, view, row)
	return err
}

// addWall puts what holds back a wake that is due on a view.
func (e *Env) addWall(name string, view map[string]any, row *db.AgentState) error {
	w, err := e.Delivery.Wall(e.DB, name, row)
	if err != nil || w == nil {
		return err
	}
	var clock any
	if w.Clock != 0 {
		clock = w.Clock
	}
	view["wall"] = map[string]any{"account": w.Account, "clock": clock, "assumed": w.Assumed}
	return nil
}

// screenIdle is whether a stored wire word is the screen word IDLE, as agentview.screen_word reads it back: idle, no_terminal and any
// word not named here.
func screenIdle(word any) bool {
	switch word {
	case "running", "queued", "parked", "waiting", "stalled":
		return false
	}
	return true
}
