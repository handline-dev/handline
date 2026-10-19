package db

import (
	"regexp"
	"strings"
)

const (
	// MsgPrefix begins every message id an agent sees: msg_ and a prefix of the request's ref.
	MsgPrefix = "msg_"
	// MsgMinPrefix is the shortest ref prefix a message is named by, as git names a commit.
	MsgMinPrefix = 7
)

// MsgName is how request rid is named to agents: msg_ and the shortest prefix of its ref, of at least MsgMinPrefix, that begins no other
// ref. A request with no row is "an unknown message".
func MsgName(q Q, rid int64) string {
	var ref string
	if q.QueryRow("SELECT COALESCE(ref, '') FROM requests WHERE id = ?", rid).Scan(&ref) != nil || ref == "" {
		return "an unknown message"
	}
	return MsgNameOf(q, ref)
}

// MsgNameOf is MsgName for a request whose ref is in hand.
func MsgNameOf(q Q, ref string) string {
	ref = strings.ToLower(ref)
	n := min(MsgMinPrefix, len(ref))
	for ; n < len(ref); n++ {
		var others int
		p := ref[:n]
		if q.QueryRow("SELECT COUNT(*) FROM requests WHERE ref >= ? AND ref < ? AND ref != ?", p, p+"g", ref).Scan(&others) != nil || others == 0 {
			break
		}
	}
	return MsgPrefix + ref[:n]
}

// Name is MsgName for a row in hand.
func (r *Request) Name(q Q) string {
	if r.Ref == "" {
		return MsgName(q, r.ID)
	}
	return MsgNameOf(q, r.Ref)
}

var (
	msgToken  = regexp.MustCompile(`(?i)\bmsg_[0-9a-f]+\b`)
	hashToken = regexp.MustCompile(`(?i)\b[0-9a-f]{7,12}\b`)
)

// MessageNamedIn is the first message id text names, "" for none: any msg_<hex>, or a bare 7 to 12 hex characters that begin a stored
// ref. A longer hex word, such as a full commit hash, is not one.
func MessageNamedIn(q Q, text string) string {
	if m := msgToken.FindString(text); m != "" {
		return m
	}
	for _, m := range hashToken.FindAllString(text, -1) {
		p := strings.ToLower(m)
		var n int
		if q.QueryRow("SELECT COUNT(*) FROM requests WHERE ref >= ? AND ref < ?", p, p+"g").Scan(&n) == nil && n > 0 {
			return m
		}
	}
	return ""
}
