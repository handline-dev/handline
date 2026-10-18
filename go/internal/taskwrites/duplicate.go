package taskwrites

import (
	"database/sql"
	"fmt"
	"regexp"
	"strings"

	"github.com/handline/handline/go/internal/rpc"
)

// A new task whose title repeats an open task under the same parent (or the parent itself) is refused, naming that task: a duplicate child
// sat open for a day holding its parent and everything blocked on the parent. duplicate_ok opens it anyway.

// DuplicateGate is the refusal's gate; its data names the open task as `task`.
const DuplicateGate = "duplicate_title"

var (
	storyPrefix = regexp.MustCompile(`^\s*story\s+\d+(-\d+)?\s*:\s*`)
	nonWord     = regexp.MustCompile(`[^\pL\pN]+`)
	shaWord     = regexp.MustCompile(`^[0-9a-f]{7,40}$`)
	digit       = regexp.MustCompile(`[0-9]`)
)

// titleWords is a title as compared: lower case, without a leading "Story x-y:", cut at every run that is not a letter or a digit, and
// without the commit shas that tell two rounds of one review apart.
func titleWords(title string) []string {
	t := nonWord.ReplaceAllString(storyPrefix.ReplaceAllString(strings.ToLower(title), ""), " ")
	var out []string
	for _, w := range strings.Fields(t) {
		if !(shaWord.MatchString(w) && digit.MatchString(w)) {
			out = append(out, w)
		}
	}
	return out
}

// sameTitle: both have three words or more, start with the same word, and at least 80% of the shorter one's distinct words are in the
// other. Over the task history (2,450 pairs of tasks open together under one parent) this flags 20, among them the duplicate it is for;
// most of the rest are a second review of one task opened while the first was still open.
func sameTitle(a, b string) bool {
	wa, wb := titleWords(a), titleWords(b)
	if len(wa) < 3 || len(wb) < 3 || wa[0] != wb[0] {
		return false
	}
	sa, sb := map[string]bool{}, map[string]bool{}
	for _, w := range wa {
		sa[w] = true
	}
	for _, w := range wb {
		sb[w] = true
	}
	shared := 0
	for w := range sa {
		if sb[w] {
			shared++
		}
	}
	return float64(shared) >= 0.8*float64(min(len(sa), len(sb)))
}

// duplicateRefusal is the refusal of a new child of parent titled title, nil when no open task under parent, nor parent itself, repeats it.
func duplicateRefusal(tx *sql.Tx, parent int64, title string) error {
	rows, err := tx.Query("SELECT id, title FROM tasks WHERE (id = ? OR parent_task = ?) AND done_evidence IS NULL AND discarded_by IS NULL ORDER BY id",
		parent, parent)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var id int64
		var t string
		if err := rows.Scan(&id, &t); err != nil {
			return err
		}
		if sameTitle(title, t) {
			where := fmt.Sprintf("under T%d", parent)
			if id == parent {
				where = "its parent"
			}
			e := rpc.Refused(fmt.Sprintf("T%d (%s) is open with nearly this title: %q", id, where, t), DuplicateGate,
				"add to that task instead, or open this one anyway with --duplicate-ok")
			e.Data["task"] = id
			return e
		}
	}
	return rows.Err()
}
