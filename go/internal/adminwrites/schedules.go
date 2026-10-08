package adminwrites

import (
	"errors"
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// schedule.add, schedule.edit and schedule.remove: a schedule's id is the number of its agent plus its cron and message, so changing either
// makes a new one. add and edit answer the id the schedule has now.

func (e *Env) reader() *reads.Env { return &reads.Env{DB: e.DB, Home: e.Home} }

// valueError is what schedules raises as a ValueError: bad_request naming the cron.
func valueError(err error) error {
	var bad *reads.ScheduleError
	if errors.As(err, &bad) {
		return badParam(bad.Msg, "cron")
	}
	return err
}

func normal(cron, message string) reads.Schedule {
	return reads.Schedule{Cron: strings.Join(strings.Fields(cron), " "), Message: strings.Join(strings.Fields(message), " ")}
}

func has(items []reads.Schedule, s reads.Schedule) bool {
	for _, it := range items {
		if it == s {
			return true
		}
	}
	return false
}

// write keeps the agent's schedules in its agent.yaml, validated; none removes the key.
func (e *Env) writeSchedules(agent string, items []reads.Schedule) error {
	var value any
	if len(items) > 0 {
		list := make([]any, len(items))
		for i, it := range items {
			list[i] = map[string]any{"cron": it.Cron, "message": it.Message}
		}
		value = rpc.PyJSON(list, true, true)
	}
	return team.NewRoster(e.Home).SetKeys(agent, []yamlcompat.Pair{{Key: "schedules", Val: value}})
}

func (e *Env) scheduleList(agent string, add *reads.Schedule) ([]reads.Schedule, error) {
	items, err := e.reader().LoadSchedules(agent)
	if err != nil {
		return nil, valueError(err)
	}
	if add != nil {
		items = append(items, *add)
	}
	return items, nil
}

// find is (agent, 1-based index, schedule) of the schedule the call names; not_found when no agent has it.
func (e *Env) findSchedule(p map[string]any) (string, int, reads.Schedule, error) {
	sid, ok := rpc.AsInt(p["schedule"])
	if !ok {
		return "", 0, reads.Schedule{}, badParam("schedule is a schedule id", "schedule")
	}
	for _, agent := range paths.ListAgents(e.Home) {
		for i, it := range e.reader().SchedulesOf(agent) {
			if reads.ScheduleID(agent, it) == sid {
				return agent, i + 1, it, nil
			}
		}
	}
	return "", 0, reads.Schedule{}, notFound("schedule", fmt.Sprint(sid), fmt.Sprintf("no schedule %d", sid))
}

func (e *Env) scheduleAdd(c *rpc.Conn, p map[string]any) (any, error) {
	agent, err := e.agentNamed(p, "target")
	if err != nil {
		return nil, err
	}
	cron, _, err := text(p, "cron", true)
	if err != nil {
		return nil, err
	}
	message, _, err := text(p, "message", true)
	if err != nil {
		return nil, err
	}
	item := normal(cron, message)
	have, err := e.scheduleList(agent, nil)
	if err != nil {
		return nil, err
	}
	if has(have, item) {
		return nil, refused(agent+" has this schedule already", "state")
	}
	next, err := reads.ParseSchedules(append(have, reads.Schedule{Cron: cron, Message: message}))
	if err != nil {
		return nil, valueError(err)
	}
	if err := e.writeSchedules(agent, next); err != nil {
		return nil, err
	}
	return wrote(c, fmt.Sprintf("%s: scheduled %s", agent, item.Cron), map[string]any{"schedule": reads.ScheduleID(agent, item)})
}

func (e *Env) scheduleEdit(c *rpc.Conn, p map[string]any) (any, error) {
	agent, index, was, err := e.findSchedule(p)
	if err != nil {
		return nil, err
	}
	target := agent
	if p["target"] != nil {
		if target, err = e.agentNamed(p, "target"); err != nil {
			return nil, err
		}
	}
	cron, message := was.Cron, was.Message
	if s, ok, err := text(p, "cron", false); err != nil {
		return nil, err
	} else if ok {
		cron = s
	}
	if s, ok, err := text(p, "message", false); err != nil {
		return nil, err
	} else if ok {
		message = s
	}
	item := normal(cron, message)
	if target == agent {
		have, err := e.scheduleList(agent, nil)
		if err != nil {
			return nil, err
		}
		if item != was && has(have, item) {
			return nil, refused(agent+" has this schedule already", "state")
		}
		if index < 1 || index > len(have) {
			return nil, badParam(fmt.Sprintf("edit needs --index 1..%d", len(have)), "cron")
		}
		have[index-1] = reads.Schedule{Cron: cron, Message: message}
		next, err := reads.ParseSchedules(have)
		if err != nil {
			return nil, valueError(err)
		}
		if err := e.writeSchedules(agent, next); err != nil {
			return nil, err
		}
	} else {
		there, err := e.scheduleList(target, nil)
		if err != nil {
			return nil, err
		}
		if has(there, item) {
			return nil, refused(target+" has this schedule already", "state")
		}
		next, err := reads.ParseSchedules(append(there, reads.Schedule{Cron: cron, Message: message}))
		if err != nil {
			return nil, valueError(err)
		}
		if err := e.writeSchedules(target, next); err != nil {
			return nil, err
		}
		if err := e.removeSchedule(agent, index); err != nil {
			return nil, err
		}
	}
	return wrote(c, fmt.Sprintf("%s: schedule is now %s", target, item.Cron), map[string]any{"schedule": reads.ScheduleID(target, item)})
}

func (e *Env) removeSchedule(agent string, index int) error {
	have, err := e.scheduleList(agent, nil)
	if err != nil {
		return err
	}
	if index < 1 || index > len(have) {
		return badParam(fmt.Sprintf("remove needs --index 1..%d", len(have)), "cron")
	}
	return e.writeSchedules(agent, append(have[:index-1:index-1], have[index:]...))
}

func (e *Env) scheduleRemove(c *rpc.Conn, p map[string]any) (any, error) {
	agent, index, item, err := e.findSchedule(p)
	if err != nil {
		return nil, err
	}
	if err := e.removeSchedule(agent, index); err != nil {
		return nil, err
	}
	return wrote(c, fmt.Sprintf("%s: removed %s", agent, item.Cron), nil)
}
