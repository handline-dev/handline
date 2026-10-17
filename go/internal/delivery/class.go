package delivery

import (
	"regexp"
	"strings"
	"unicode"
)

// Class is when a message to an agent reaches it.
type Class int

const (
	// ClassNow is typed at once, mid-turn too.
	ClassNow Class = iota
	// ClassBatch is a notice: it waits with the target's other notices and they are typed as one digest once the digest window has run and
	// the target is at its prompt, never mid-turn (supervisor.SweepDigests). It also rides on the target's next delivery that is typed.
	ClassBatch
)

// Kind is what a message from the task board says, for its class.
type Kind int

const (
	Result     Kind = iota // a task's or a child's result, to someone who did not ask for it
	TaskChange             // a task's field or queue change, to whoever watches it
	Memo                   // a memo or comment on a task, to a reader
)

// ClassOf is the one rule for which of the board's messages wait for a digest. aimed: the message is for this target to act on (the one
// who asked for the result, a holder or an @mention of a memo, a memo told now). failed: a result that says it failed, is blocked or FATAL,
// which is not information but something gone wrong.
func ClassOf(kind Kind, aimed, failed bool) Class {
	if aimed || kind == Result && failed {
		return ClassNow
	}
	return ClassBatch
}

var failWord = regexp.MustCompile(`(?i)^(fail(?:ed|ure)?|blocked|fatal)`)

// Failed is whether a task's closing words say it failed, is blocked or FATAL (urgent_result).
func Failed(result string) bool {
	s := strings.TrimSpace(result)
	m := failWord.FindString(s)
	if m == "" {
		return false
	}
	rest := []rune(s[len(m):])
	return len(rest) == 0 || !(unicode.IsLetter(rest[0]) || unicode.IsDigit(rest[0]) || rest[0] == '_')
}
