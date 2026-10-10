package supervisor

import (
	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/team"
)

// SystemSay is a say from a part of the daemon itself (a land's result, from "land") to an agent: a fire-and-forget say, written to its
// inbox and typed at once, with no routing decision and nobody parked on it (daemon/lands.py _tell).
func (e *Env) SystemSay(requester, target, body string) error {
	return e.SystemSayThen(requester, target, body, nil)
}

// SystemSayThen is SystemSay that hands the new request's id to recorded once it is written to the inbox and before it is told, for a caller
// that keeps the id (the schedules' cursor).
func (e *Env) SystemSayThen(requester, target, body string, recorded func(rid int64)) (err error) {
	defer Catch(&err)
	q := db.Q(e.DB)
	rid := must(db.AddRequest(q, db.NewRequest{Requester: requester, Target: target, Body: body, Response: "fire_and_forget", Kind: "say"}))
	must(db.MarkSendNow(q, rid))
	check(e.deliver(rid, requester, target, body, "", team.Decision{Allowed: true, Reason: requester, Kind: ptr("say"), Response: "fire_and_forget"}))
	if recorded != nil {
		recorded(rid)
	}
	e.after(rid, target)
	return nil
}
