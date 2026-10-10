package supervisor

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
)

// SystemSay is a say from a part of the daemon itself (a land's result, from "land") to an agent: a fire-and-forget say, written to its
// inbox and typed at once, with no routing decision and nobody parked on it (daemon/lands.py _tell).
func (e *Env) SystemSay(requester, target, body string) (err error) {
	defer Catch(&err)
	q := db.Q(e.DB)
	rid := must(db.AddRequest(q, db.NewRequest{Requester: requester, Target: target, Body: body, Response: "fire_and_forget", Kind: "say"}))
	must(db.MarkSendNow(q, rid))
	check(e.deliver(rid, requester, target, body, "", team.Decision{Allowed: true, Reason: requester, Kind: ptr("say"), Response: "fire_and_forget"}))
	e.after(rid, target)
	return nil
}
