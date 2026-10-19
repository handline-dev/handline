// Package msgtest is for tests: a message's printed name holds a random ref, so a test reads printed text back as #<id>.
package msgtest

import (
	"fmt"
	"regexp"

	"github.com/handline/handline/go/internal/db"
)

var name = regexp.MustCompile(db.MsgPrefix + `([0-9a-f]+)`)

// IDs is text with every msg_<hash> that begins exactly one stored ref written as #<id> of that request; any other is left as it is.
func IDs(q db.Q, text string) string {
	return name.ReplaceAllStringFunc(text, func(m string) string {
		p := m[len(db.MsgPrefix):]
		var ids []int64
		rows, err := q.Query("SELECT id FROM requests WHERE ref >= ? AND ref < ?", p, p+"g")
		if err != nil {
			return m
		}
		defer rows.Close()
		for rows.Next() {
			var id int64
			if rows.Scan(&id) == nil {
				ids = append(ids, id)
			}
		}
		if len(ids) != 1 {
			return m
		}
		return fmt.Sprintf("#%d", ids[0])
	})
}
