package reads

import (
	"errors"
	"fmt"
	"strings"
)

// The schedules as the writes need them (handline/team/schedules.py): read, validated and numbered the way schedules.list shows them.

// Schedule is one {cron, message}, as it is kept.
type Schedule struct{ Cron, Message string }

// ScheduleError is a `schedules` value that is wrong: what schedules.parse raises as a ValueError.
type ScheduleError struct{ Msg string }

func (e *ScheduleError) Error() string { return e.Msg }

// ScheduleID is the integer the spec gives a schedule.
func ScheduleID(agent string, s Schedule) int64 {
	return scheduleID(agent, schedule{s.Cron, s.Message})
}

// ScheduleSID is what identifies a schedule: editing its cron or its message makes it a new one.
func ScheduleSID(s Schedule) string { return schedule{s.Cron, s.Message}.sid() }

// NextCronAfter is the first minute after ts the cron expression names, false when it names none within eight years or does not parse.
func NextCronAfter(expr string, ts int64) (int64, bool) {
	c, err := parseCron(expr)
	if err != nil {
		return 0, false
	}
	return c.nextAfter(ts)
}

// LoadSchedules is an agent's schedules; a *ScheduleError says what is wrong with its value, any other error is an agent.yaml that cannot be read.
func (e *Env) LoadSchedules(agent string) ([]Schedule, error) {
	items, err := e.loadSchedules(agent)
	var bad badValue
	if errors.As(err, &bad) {
		return nil, &ScheduleError{string(bad)}
	}
	return toSchedules(items), err
}

// SchedulesOf is LoadSchedules, and none for an agent whose agent.yaml or value cannot be read: the sweep's view.
func (e *Env) SchedulesOf(agent string) []Schedule {
	items, err := e.LoadSchedules(agent)
	if err != nil {
		return nil
	}
	return items
}

func toSchedules(items []schedule) []Schedule {
	out := make([]Schedule, len(items))
	for i, s := range items {
		out[i] = Schedule{s.cron, s.message}
	}
	return out
}

// ParseSchedules validates the list and keeps each cron and message with their spaces squeezed (schedules.parse).
func ParseSchedules(items []Schedule) ([]Schedule, error) {
	out := make([]Schedule, 0, len(items))
	for i, it := range items {
		if _, err := parseCron(it.Cron); err != nil {
			return nil, &ScheduleError{err.Error()}
		}
		message := strings.Join(strings.Fields(it.Message), " ")
		if message == "" {
			return nil, &ScheduleError{fmt.Sprintf("schedule %d has no message", i+1)}
		}
		out = append(out, Schedule{strings.Join(strings.Fields(it.Cron), " "), message})
	}
	return out, nil
}
