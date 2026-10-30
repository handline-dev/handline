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

// emptyBody stands in for a request with no body: a task shows its title, anything else "(empty)".
func emptyBody(q db.Q, r *db.Request) string {
	if t := taskOf(q, r); t != nil && r.Kind.String == "task" {
		return fmt.Sprintf("T%d (no body) Title: %s", t.ID, t.Title)
	}
	return "(empty)"
}

// AnswerVerb is how the agent answers what it was just told: a task is handed on or finished; the founder is answered through ocp;
// another agent's request by a reply that names it; a say or a report asks nothing.
func answerVerb(q db.Q, head *db.Request) string {
	if head.Kind.String == "task" {
		if t := taskOf(q, head); t != nil {
			return fmt.Sprintf("you own T%d now: hand it on with `hl task handoff T%d <agent> \"<note>\"`, or `hl task done T%d` if you are its last step", t.ID, t.ID, t.ID)
		}
	}
	if head.Requester == db.Human {
		return fmt.Sprintf("answer the founder now, before other work: `hl say human --parent %s \"...\" --task T<n>` (or `--no-task \"<why>\"`) - if it is work, one line on what you will do or who has it", head.Name(q))
	}
	if head.Kind.String == "say" {
		return "it asks nothing"
	}
	return fmt.Sprintf("answer with `hl say %s --parent %s \"<answer>\"`", head.Requester, head.Name(q))
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
func foldedNotices(q db.Q, agent string) []*db.Request {
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
			lines[i] = fmt.Sprintf("%s… (hl read %s)", strings.TrimRight(string([]rune(t)[:NoticeShown]), " \t\n\r\v\f"), r.Name(q))
		}
	}
	left := len(must(db.PendingNotices(q, agent))) - len(folded)
	more := ""
	if left > 0 {
		more = fmt.Sprintf(" (+%d more: `hl inbox --all`)", left)
	}
	return " Also: " + strings.Join(lines, " | ") + more
}

// InReplyTo is `in reply to msg_<hash>: <first line of it>` for a request with a parent; "" for one without, or whose parent is gone.
func inReplyTo(q db.Q, r *db.Request) string {
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
	return fmt.Sprintf("in reply to %s%s", parent.Name(q), first)
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
func tellText(q db.Q, head *db.Request, depth int, aheadOf *db.Request, parked bool, folded []*db.Request, images string) string {
	more := depth - 1
	var shown string
	tid, _, title, note, isHandoff := HandoffParts(head.Body)
	if head.Kind.String == "task" && isHandoff {
		note = textsafe.Lines(note)
		if note == "" {
			note = "(no note)"
		}
		title = textsafe.Oneline(title)
		if utf8.RuneCountInString(title) > TitleShown {
			title = strings.TrimRight(string([]rune(title)[:TitleShown]), " \t\n\r\v\f") + "…"
		}
		sep := " " // after a note of several lines, the title gets a line of its own
		if strings.Contains(note, "\n") {
			sep = "\n"
		}
		shown = fmt.Sprintf("T%d handed to you. Note: %s%sTitle: %s", tid, note, sep, title)
	} else {
		body := strings.TrimSpace(textsafe.Lines(head.Body))
		if body == "" && images == "" {
			body = emptyBody(q, head)
		}
		digest := head.Requester == db.NoticeRequester && strings.HasPrefix(body, db.DigestHead)
		if digest { // a line per notice, as DigestText wrote it
			lines := strings.Split(strings.TrimSpace(head.Body), "\n")
			for i, l := range lines {
				lines[i] = textsafe.Oneline(l)
			}
			body = strings.Join(lines, "\n")
		}
		// A reply to an earlier request is a verdict or an answer: cut, it hides the verdict.
		whole := head.ParentID.Valid || digest
		shown = body
		if !whole && utf8.RuneCountInString(body) > 400 {
			shown = string([]rune(body)[:400]) + "…"
		}
		shown = quoted(head) + shown
		if images != "" { // after the cut: the paths are what the agent needs
			shown = strings.TrimSpace(shown + "\n" + images)
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
		tail = fmt.Sprintf(" [Queued behind %s, which is still open: the next task comes when you hand it on or finish it.]", aheadOf.Name(q))
	case aheadOf != nil:
		if head.ParentID.Valid && head.ParentID.Int64 == aheadOf.ID {
			tail = fmt.Sprintf(" [A follow-up to %s, which is still open.]", aheadOf.Name(q))
		} else {
			tail = fmt.Sprintf(" [Sent now, ahead of %s, which is still open.]", aheadOf.Name(q))
		}
	}
	reply := ""
	if r := inReplyTo(q, head); r != "" {
		reply = " (" + r + ")"
	}
	end := ", or `hl inbox` any time."
	if head.Requester == db.Human || head.Kind.String == "say" {
		end = "."
	}
	return fmt.Sprintf("%s from %s%s, waiting for you:\n%s%s\n`hl read %s` shows it whole; %s%s",
		head.Name(q), head.Requester, reply, shown, tail, head.Name(q), answerVerb(q, head), end)
}

// BatchText is one message for several queued tasks: a line each, then how to work them.
func batchText(q db.Q, batch []*db.Request, folded []*db.Request, aheadOf *db.Request) string {
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
			if strings.TrimSpace(text) == "" {
				text = emptyBody(q, r)
			}
		}
		text = strings.TrimSpace(textsafe.Oneline(text))
		if utf8.RuneCountInString(text) > BatchLine {
			text = strings.TrimRight(string([]rune(text)[:BatchLine]), " \t\n\r\v\f") + "…"
		}
		lines[i] = fmt.Sprintf("%s from %s: %s", r.Name(q), r.Requester, text)
	}
	head := fmt.Sprintf("%d tasks waiting for you:", len(batch))
	if aheadOf != nil {
		head = fmt.Sprintf("%d more tasks queued behind %s, which is still open:", len(batch), aheadOf.Name(q))
	}
	foot := "`hl read <id>` shows one whole. You do one at a time, in this order: each ends with `hl task done T<n>` or `hl task handoff T<n> <agent> \"<note>\"`." +
		noticesText(q, batch[0].Target, folded)
	return strings.Join(append(append([]string{head}, lines...), foot), "\n")
}
