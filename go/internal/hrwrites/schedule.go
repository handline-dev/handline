package hrwrites

import (
	"fmt"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/textsafe"
)

// ScheduleMessageMax is how long a proposed schedule's message may be, in characters.
const ScheduleMessageMax = 1000

// ScheduleMinGapS is the shortest time a proposed cron may leave between two firings: an agent may ask for hourly work, not for a message a minute.
const ScheduleMinGapS = 3600

// cronGapProblem is why cron fires more often than ScheduleMinGapS, or "".
func cronGapProblem(cron string) string {
	at := time.Now().Unix()
	prev, ok := reads.NextCronAfter(cron, at)
	for i := 0; ok && i < 48; i++ {
		next, ok2 := reads.NextCronAfter(cron, prev)
		if !ok2 {
			break
		}
		if next-prev < ScheduleMinGapS {
			return fmt.Sprintf("%q fires every %d minutes at its fastest; a proposed schedule fires at most once an hour", cron, (next-prev)/60)
		}
		prev = next
	}
	return ""
}

// scheduleChange checks one schedule add, edit or remove of agent against the schedules it has and the open proposals, and returns the
// `set.` fields doSchedule applies, or why not. args: action (add, edit, remove), cron and message (the new ones), was_cron and was_message
// (which existing one an edit or remove means; the message may be left out when the cron is unique).
func (e *Env) scheduleChange(agent string, args map[string]any) (map[string]string, string) {
	action := argStr(args, "action")
	if action != "add" && action != "edit" && action != "remove" {
		return nil, "a schedule change is add, edit or remove (--remove, or --was to edit)"
	}
	have, err := e.reader().LoadSchedules(agent)
	if err != nil {
		return nil, fmt.Sprintf("%s's schedules cannot be read: %v", agent, err)
	}
	cron, message := strings.Join(strings.Fields(argStr(args, "cron")), " "), strings.Join(strings.Fields(argStr(args, "message")), " ")
	sets := map[string]string{"action": action}
	var was *reads.Schedule
	if action != "add" {
		wc, wm := strings.Join(strings.Fields(argStr(args, "was_cron")), " "), strings.Join(strings.Fields(argStr(args, "was_message")), " ")
		if wc == "" {
			return nil, "say which schedule: --was '<its cron>' (and --was-message if two share it)"
		}
		var hits []reads.Schedule
		for _, it := range have {
			if it.Cron == wc && (wm == "" || it.Message == wm) {
				hits = append(hits, it)
			}
		}
		switch len(hits) {
		case 0:
			return nil, fmt.Sprintf("%s has no schedule with cron %q%s", agent, wc, map[bool]string{true: " and that message", false: ""}[wm != ""])
		case 1:
			was = &hits[0]
		default:
			return nil, fmt.Sprintf("%s has %d schedules with cron %q: say which with --was-message", agent, len(hits), wc)
		}
		sets["sid"], sets["message_was"] = reads.ScheduleSID(*was), was.Message
		sets["cron_was"] = was.Cron
		if action == "edit" {
			if cron == "" {
				cron = was.Cron
			}
			if message == "" {
				message = was.Message
			}
			if cron == was.Cron && message == was.Message {
				return nil, "that is the schedule it already is: nothing to edit"
			}
		}
	}
	if action != "remove" {
		if message == "" {
			return nil, "a schedule needs --message: the text typed at the agent each time"
		}
		if n := utf8.RuneCountInString(message); n > ScheduleMessageMax {
			return nil, fmt.Sprintf("the message is %d characters; a schedule's message is at most %d", n, ScheduleMessageMax)
		}
		if textsafe.HasUnsafe(message) || textsafe.HasUnsafe(cron) {
			return nil, "the cron or message has characters that cannot be kept"
		}
		if _, err := reads.ParseSchedules([]reads.Schedule{{Cron: cron, Message: message}}); err != nil {
			return nil, err.Error()
		}
		if why := cronGapProblem(cron); why != "" {
			return nil, why
		}
		for _, it := range have {
			if it.Cron == cron && it.Message == message && (was == nil || reads.ScheduleSID(it) != reads.ScheduleSID(*was)) {
				return nil, agent + " has this schedule already"
			}
		}
		if action == "add" {
			sets["sid"] = reads.ScheduleSID(reads.Schedule{Cron: cron, Message: message})
		}
		sets["cron"], sets["message"] = cron, message
	}
	for _, p := range e.listing() {
		if p.kind() == "schedule" && p.str("state") == "proposed" && p.str("agent") == agent && p.sets()["sid"] == sets["sid"] {
			return nil, fmt.Sprintf("proposal %s already asks for that schedule of %s: withdraw it first (`hl hr withdraw %s`) to change it", p.id(), agent, p.id())
		}
	}
	return sets, ""
}
