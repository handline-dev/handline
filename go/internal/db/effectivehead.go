package db

// HeadTaskBlocked is delivery.head_task_blocked: is the head's own task on record as waited on, blocked by another open task or
// held up on an ask the agent itself sent that is linked to the same task. A head with no task is never this.
func HeadTaskBlocked(q Q, agent string, head *Request) (bool, error) {
	tasks, err := TasksForRequest(q, head.ID)
	if err != nil {
		return false, err
	}
	for _, t := range tasks {
		blockers, err := TaskBlockers(q, t.ID)
		if err != nil {
			return false, err
		}
		if len(blockers) > 0 {
			return true, nil
		}
		reqs, err := TaskRequests(q, t.ID)
		if err != nil {
			return false, err
		}
		for _, r := range reqs {
			if r.Requester == agent && r.ID != head.ID && r.State == "queued" {
				return true, nil
			}
		}
	}
	return false, nil
}

// EffectiveHead is HeadOfQueue unless it is blocked on record: then the next request behind it, so the one-at-a-time rule falls on
// whatever the agent can actually act on. Only one level.
func EffectiveHead(q Q, agent string) (*Request, error) {
	head, err := HeadOfQueue(q, agent, 0)
	if err != nil || head == nil {
		return head, err
	}
	blocked, err := HeadTaskBlocked(q, agent, head)
	if err != nil {
		return nil, err
	}
	if blocked {
		return HeadOfQueue(q, agent, head.ID)
	}
	return head, nil
}
