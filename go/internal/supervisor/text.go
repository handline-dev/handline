package supervisor

import (
	"fmt"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/textsafe"
)

// The words typed at an agent (daemon/delivery.py: tell_text, batch_text, answers_text and what they are built from).

const (
	// TitleShown is how much of a handed-off task's title a tell shows.
	TitleShown = 60
	// NoticesShown is how many waiting notices one typed delivery carries; NoticeShown how much of each.
	NoticesShown = 3
	NoticeShown  = 160
	// AskedShown is how much of the original question an answer line quotes.
	AskedShown = 40
	// BatchLine is how much of each task a batch message shows.
	BatchLine = 160
)

var handoffBody = regexp.MustCompile(`(?s)\AT(\d+) handed to you by (\S+?): (.*?)\n\n(.*)\z`)

// HandoffParts is (task id, who handed it, title, note) of a task handoff's body (actions.tasks.task_handoff); ok false for any other.
func HandoffParts(body string) (tid int64, by, title, note string, ok bool) {
	m := handoffBody.FindStringSubmatch(body)
	if m == nil {
		return 0, "", "", "", false
	}
	fmt.Sscan(m[1], &tid)
	return tid, m[2], strings.TrimSpace(m[3]), strings.TrimSpace(m[4]), true
}

// taskOf is the task a kind-task request is about: the one it is linked to, else the one its body names.
func taskOf(q db.Q, r *db.Request) *db.Task {
	if linked := must(db.TasksForRequest(q, r.ID)); len(linked) > 0 {
		return linked[len(linked)-1]
	}
	if tid, _, _, _, ok := HandoffParts(r.Body); ok {
		return must(db.GetTask(q, tid))
	}
	return nil
}

// AnswerVerb is how the agent answers what it was just told: a task is handed on or finished; the founder is answered through ocp;
// another agent's request by a reply that names it; a say or a report asks nothing.
func AnswerVerb(q db.Q, head *db.Request) string {
	if head.Kind.String == "task" {
		if t := taskOf(q, head); t != nil {
			return fmt.Sprintf("you own T%d now: hand it on with `hl task handoff T%d <agent> \"<note>\"`, or `hl task done T%d` if you are its last step", t.ID, t.ID, t.ID)
		}
	}
	if head.Requester == db.Human {
		return fmt.Sprintf("answer the founder now, before other work: `hl say human --parent %d \"...\" --task T<n>` (or `--no-task \"<why>\"`) - if it is work, one line on what you will do or who has it", head.ID)
	}
	if head.Kind.String == "say" {
		return "it asks nothing"
	}
	return fmt.Sprintf("answer with `hl say %s --parent %d \"<answer>\"`", head.Requester, head.ID)
}

// MoreWaiting is what an agent is told about the queue behind the request it reads, "" for nothing.
func MoreWaiting(kind string, more int) string {
	if more == 0 || kind == "say" {
		return ""
	}
	if kind == "task" {
		return fmt.Sprintf("%d more waiting - the next comes when you hand this on or finish it.", more)
	}
	return fmt.Sprintf("%d more waiting - the next comes when you answer this one.", more)
}

// FoldedNotices are the notices the next typed delivery to the agent carries; the caller marks them shown once the send went through.
func FoldedNotices(q db.Q, agent string) []*db.Request {
	rows := must(db.PendingNotices(q, agent))
	if len(rows) > NoticesShown {
		rows = rows[:NoticesShown]
	}
	return rows
}

func ellipsize(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return strings.TrimRight(string([]rune(s)[:n]), " \t\n\r\v\f") + "…"
}

func noticesText(q db.Q, agent string, folded []*db.Request) string {
	if len(folded) == 0 {
		return ""
	}
	lines := make([]string, len(folded))
	for i, r := range folded {
		t := textsafe.Oneline(r.Body)
		if utf8.RuneCountInString(t) <= NoticeShown { // a cut notice names its id: the cut part held the result's paths and caveats
			lines[i] = t
		} else {
			lines[i] = fmt.Sprintf("%s… (hl read %d)", strings.TrimRight(string([]rune(t)[:NoticeShown]), " \t\n\r\v\f"), r.ID)
		}
	}
	left := len(must(db.PendingNotices(q, agent))) - len(folded)
	more := ""
	if left > 0 {
		more = fmt.Sprintf(" (+%d more: `hl inbox --all`)", left)
	}
	return " Also: " + strings.Join(lines, " | ") + more
}

// InReplyTo is `in reply to #M: <first line of M>` for a request with a parent; "" for one without, or whose parent is gone.
func InReplyTo(q db.Q, r *db.Request) string {
	if !r.ParentID.Valid {
		return ""
	}
	parent := must(db.GetRequest(q, r.ParentID.Int64))
	if parent == nil {
		return ""
	}
	first := ""
	if lines := splitLines(parent.Body); len(lines) > 0 {
		first = strings.TrimSpace(textsafe.Oneline(lines[0]))
	}
	first = ellipsize(first, AskedShown)
	if first != "" {
		first = ": " + first
	}
	return fmt.Sprintf("in reply to #%d%s", parent.ID, first)
}

// splitLines is Python's str.splitlines for the separators a request body holds.
func splitLines(s string) []string {
	if s == "" {
		return nil
	}
	s = strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\r", "\n")
	lines := strings.Split(s, "\n")
	if lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	return lines
}

// TellText is the message that tells an agent about head, as typed. aheadOf is the head a "send now" request is jumping, or with parked
// the open head the task is queued behind. folded is FoldedNotices, carried on the same line.
func TellText(q db.Q, head *db.Request, depth int, aheadOf *db.Request, parked bool, folded []*db.Request) string {
	more := depth - 1
	var shown string
	tid, _, title, note, isHandoff := HandoffParts(head.Body)
	if head.Kind.String == "task" && isHandoff {
		note = textsafe.Oneline(note)
		if note == "" {
			note = "(no note)"
		}
		title = textsafe.Oneline(title)
		if utf8.RuneCountInString(title) > TitleShown {
			title = strings.TrimRight(string([]rune(title)[:TitleShown]), " \t\n\r\v\f") + "…"
		}
		shown = fmt.Sprintf("T%d handed to you. Note: %s Title: %s", tid, note, title)
	} else {
		body := strings.TrimSpace(textsafe.Oneline(head.Body))
		if body == "" {
			body = "(empty)"
		}
		// A reply to an earlier request is a verdict or an answer: cut, it hides the verdict.
		whole := head.ParentID.Valid || (head.Requester == db.NoticeRequester && strings.HasPrefix(body, db.DigestHead))
		shown = body
		if !whole && utf8.RuneCountInString(body) > 400 {
			shown = string([]rune(body)[:400]) + "…"
		}
	}
	if head.Kind.String == "task" {
		if t := taskOf(q, head); t != nil {
			if memos := must(db.MemoSummary(q, t.ID, 500)); memos != "" {
				shown += " " + memos
			}
		}
	}
	shown += noticesText(q, head.Target, folded)
	tail := ""
	if w := MoreWaiting(head.Kind.String, more); w != "" {
		tail = " [" + w + "]"
	}
	switch {
	case parked:
		tail = fmt.Sprintf(" [Queued behind #%d, which is still open: the next task comes when you hand it on or finish it.]", aheadOf.ID)
	case aheadOf != nil:
		if head.ParentID.Valid && head.ParentID.Int64 == aheadOf.ID {
			tail = fmt.Sprintf(" [A follow-up to #%d, which is still open.]", aheadOf.ID)
		} else {
			tail = fmt.Sprintf(" [Sent now, ahead of #%d, which is still open.]", aheadOf.ID)
		}
	}
	reply := ""
	if r := InReplyTo(q, head); r != "" {
		reply = " (" + r + ")"
	}
	end := ", or `hl inbox` any time."
	if head.Requester == db.Human || head.Kind.String == "say" {
		end = "."
	}
	return fmt.Sprintf("#%d from %s%s, waiting for you:\n%s%s\n`hl read %d` shows it whole; %s%s",
		head.ID, head.Requester, reply, shown, tail, head.ID, AnswerVerb(q, head), end)
}

// BatchText is one message for several queued tasks: a line each, then how to work them.
func BatchText(q db.Q, batch []*db.Request, folded []*db.Request, aheadOf *db.Request) string {
	lines := make([]string, len(batch))
	for i, r := range batch {
		var text string
		if tid, _, title, note, ok := HandoffParts(r.Body); ok {
			if note == "" {
				note = "(no note)"
			}
			text = fmt.Sprintf("T%d handed to you: %s - %s", tid, title, note)
		} else {
			text = r.Body
			if text == "" {
				text = "(empty)"
			}
		}
		text = strings.TrimSpace(textsafe.Oneline(text))
		if utf8.RuneCountInString(text) > BatchLine {
			text = strings.TrimRight(string([]rune(text)[:BatchLine]), " \t\n\r\v\f") + "…"
		}
		lines[i] = fmt.Sprintf("#%d from %s: %s", r.ID, r.Requester, text)
	}
	head := fmt.Sprintf("%d tasks waiting for you:", len(batch))
	if aheadOf != nil {
		head = fmt.Sprintf("%d more tasks queued behind #%d, which is still open:", len(batch), aheadOf.ID)
	}
	foot := "`hl read <id>` shows one whole. You do one at a time, in this order: each ends with `hl task done T<n>` or `hl task handoff T<n> <agent> \"<note>\"`." +
		noticesText(q, batch[0].Target, folded)
	return strings.Join(append(append([]string{head}, lines...), foot), "\n")
}

// AnswerLine is one answered request as the line typed at its asker, and whether the answer has more to read.
func AnswerLine(r *db.Request) (string, bool) {
	text := shownVerdict(r)
	said := r.State
	if lines := splitLines(text); len(lines) > 0 {
		said = lines[0]
	}
	asked := ellipsize(strings.TrimSpace(textsafe.Oneline(r.Body)), AskedShown)
	var head string
	if tid, _, _, _, ok := HandoffParts(r.Body); ok && r.Kind.String == "task" {
		head = fmt.Sprintf("#%d (your handoff of T%d to %s)", r.ID, tid, r.Target)
	} else if asked != "" {
		head = fmt.Sprintf("#%d (you asked: %s)", r.ID, asked)
	} else {
		head = fmt.Sprintf("#%d", r.ID)
	}
	shown := said
	if utf8.RuneCountInString(shown) > 160 {
		shown = string([]rune(shown)[:160])
	}
	return fmt.Sprintf("%s %s: %s", head, r.Target, shown), utf8.RuneCountInString(said) > 160 || strings.Contains(text, "\n")
}

// shownVerdict is db.shown_verdict: what a screen may print as this row's answer; a dismissal is a state label.
func shownVerdict(r *db.Request) string {
	dismissed, by, reason := r.Dismissal()
	if !dismissed {
		return r.Answer.String
	}
	out := "dismissed"
	if by != "" {
		out += " by " + by
	}
	if reason != "" {
		out += " - " + reason
	}
	return out
}

// AnswersText is what is typed at an agent about its answered requests.
func AnswersText(answered []*db.Request) string {
	shown, rest := answered, []*db.Request(nil)
	if len(answered) > 3 {
		shown, rest = answered[len(answered)-3:], answered[:len(answered)-3]
	}
	var lines []string
	cut := false
	for _, r := range shown {
		line, more := AnswerLine(r)
		cut = cut || more
		lines = append(lines, line)
	}
	if len(rest) > 0 {
		// Named, not counted: every id that is marked told has to have been said.
		ids := make([]string, len(rest))
		for i, r := range rest {
			ids[i] = fmt.Sprintf("#%d", r.ID)
		}
		lines = append(lines, "also answered: "+strings.Join(ids, ", "))
		cut = true
	}
	tail := ""
	if cut {
		tail = "\nRead one whole with `hl read <id>`."
	}
	return "Answers to what you asked:\n" + strings.Join(lines, " | ") + tail
}
