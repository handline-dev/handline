package supervisor

import "github.com/handline/handline/go/internal/db"

// Exported entry points over the readers that raise a failed statement internally (see Catch): each returns it as an error.

// RaiseDecision is raiseDecision with a failed statement returned as the error.
func (e *Env) RaiseDecision(q db.Q, after *After, agent, text, reason, files string, chainID string, depth int64, parentID int64, extra ...db.KV) (r0 int64, err error) {
	defer Catch(&err)
	r0 = e.raiseDecision(q, after, agent, text, reason, files, chainID, depth, parentID, extra...)
	return r0, nil
}

// Report is report with a failed statement returned as the error.
func (e *Env) Report(q db.Q, after *After, agent, text, reason string) (r0 int64, err error) {
	defer Catch(&err)
	r0 = e.report(q, after, agent, text, reason)
	return r0, nil
}

// IdleSince is idleSince with a failed statement returned as the error.
func (e *Env) IdleSince(q db.Q, agent string) (r0 int64, r1 bool, err error) {
	defer Catch(&err)
	r0, r1 = e.idleSince(q, agent)
	return r0, r1, nil
}

// HasBackgroundWork is hasBackgroundWork with a failed statement returned as the error.
func (e *Env) HasBackgroundWork(q db.Q, agent string) (r0 bool, err error) {
	defer Catch(&err)
	r0 = e.hasBackgroundWork(q, agent)
	return r0, nil
}

// BgOwesNothing is bgOwesNothing with a failed statement returned as the error.
func (e *Env) BgOwesNothing(q db.Q, agent string) (r0 bool, err error) {
	defer Catch(&err)
	r0 = e.bgOwesNothing(q, agent)
	return r0, nil
}

// BgPromptedAt is bgPromptedAt with a failed statement returned as the error.
func (e *Env) BgPromptedAt(q db.Q, agent string) (r0 int64, err error) {
	defer Catch(&err)
	r0 = e.bgPromptedAt(q, agent)
	return r0, nil
}

// BgSince is bgSince with a failed statement returned as the error.
func (e *Env) BgSince(q db.Q, agent string) (r0 int64, r1 bool, err error) {
	defer Catch(&err)
	r0, r1 = e.bgSince(q, agent)
	return r0, r1, nil
}

// BgLastShown is bgLastShown with a failed statement returned as the error.
func (e *Env) BgLastShown(q db.Q, agent string) (r0 int64, r1 bool, err error) {
	defer Catch(&err)
	r0, r1 = e.bgLastShown(q, agent)
	return r0, r1, nil
}

// BgSilentS is bgSilentS with a failed statement returned as the error.
func (e *Env) BgSilentS(q db.Q, agent string, now int64) (r0 int64, r1 bool, err error) {
	defer Catch(&err)
	r0, r1 = e.bgSilentS(q, agent, now)
	return r0, r1, nil
}

// BgStale is bgStale with a failed statement returned as the error.
func (e *Env) BgStale(q db.Q, agent string, now int64) (r0 bool, err error) {
	defer Catch(&err)
	r0 = e.bgStale(q, agent, now)
	return r0, nil
}

// BgMayStop is bgMayStop with a failed statement returned as the error.
func (e *Env) BgMayStop(q db.Q, agent string, now int64) (r0 bool, err error) {
	defer Catch(&err)
	r0 = e.bgMayStop(q, agent, now)
	return r0, nil
}

// BgHolds is bgHolds with a failed statement returned as the error.
func (e *Env) BgHolds(q db.Q, agent string, now int64) (r0 bool, err error) {
	defer Catch(&err)
	r0 = e.bgHolds(q, agent, now)
	return r0, nil
}

// ForgottenResolve is forgottenResolve with a failed statement returned as the error.
func (e *Env) ForgottenResolve(q db.Q, agent string, row *db.AgentState, now int64) (r0 *Forgotten, err error) {
	defer Catch(&err)
	r0 = e.forgottenResolve(q, agent, row, now)
	return r0, nil
}

// NudgeCount is nudgeCount with a failed statement returned as the error.
func (e *Env) NudgeCount(q db.Q, agent string, headID int64) (r0 int64, err error) {
	defer Catch(&err)
	r0 = e.nudgeCount(q, agent, headID)
	return r0, nil
}

// NudgeExhausted is nudgeExhausted with a failed statement returned as the error.
func (e *Env) NudgeExhausted(q db.Q, agent string, headID int64) (r0 bool, err error) {
	defer Catch(&err)
	r0 = e.nudgeExhausted(q, agent, headID)
	return r0, nil
}

// HeadTaskBlocked is headTaskBlocked with a failed statement returned as the error.
func (e *Env) HeadTaskBlocked(q db.Q, agent string, head *db.Request) (r0 bool, err error) {
	defer Catch(&err)
	r0 = e.headTaskBlocked(q, agent, head)
	return r0, nil
}

// EffectiveHead is effectiveHead with a failed statement returned as the error.
func (e *Env) EffectiveHead(q db.Q, agent string) (r0 *db.Request, err error) {
	defer Catch(&err)
	r0 = e.effectiveHead(q, agent)
	return r0, nil
}

// EffectiveHeadOrMarked is effectiveHeadOrMarked with a failed statement returned as the error.
func (e *Env) EffectiveHeadOrMarked(q db.Q, agent string) (r0 *db.Request, err error) {
	defer Catch(&err)
	r0 = e.effectiveHeadOrMarked(q, agent)
	return r0, nil
}

// EffectiveTaskHead is effectiveTaskHead with a failed statement returned as the error.
func (e *Env) EffectiveTaskHead(q db.Q, agent string) (r0 *db.Request, err error) {
	defer Catch(&err)
	r0 = e.effectiveTaskHead(q, agent)
	return r0, nil
}

// AgentReason is agentReason with a failed statement returned as the error.
func (e *Env) AgentReason(q db.Q, agent string, now int64) (r0 string, r1 bool, err error) {
	defer Catch(&err)
	r0, r1 = e.agentReason(q, agent, now)
	return r0, r1, nil
}

// TaskReason is taskReason with a failed statement returned as the error.
func (e *Env) TaskReason(q db.Q, t *db.Task, now int64) (r0 string, r1 bool, r2 bool, err error) {
	defer Catch(&err)
	r0, r1, r2 = e.taskReason(q, t, now)
	return r0, r1, r2, nil
}

// LandedAlready is landedAlready with a failed statement returned as the error.
func (e *Env) LandedAlready(q db.Q, agent string, rid int64) (r0 bool, err error) {
	defer Catch(&err)
	r0 = e.landedAlready(q, agent, rid)
	return r0, nil
}

// CanTypeNow is canTypeNow with a failed statement returned as the error.
func (e *Env) CanTypeNow(q db.Q, agent, handle string) (r0 bool, r1 string, err error) {
	defer Catch(&err)
	r0, r1 = e.canTypeNow(q, agent, handle)
	return r0, r1, nil
}

// ScreenDrawsSession is screenDrawsSession with a failed statement returned as the error.
func (e *Env) ScreenDrawsSession(agent string) (r0 bool, err error) {
	defer Catch(&err)
	r0 = e.screenDrawsSession(agent)
	return r0, nil
}

// SessionPresent is sessionPresent with a failed statement returned as the error.
func (e *Env) SessionPresent(agent string) (r0 bool, err error) {
	defer Catch(&err)
	r0 = e.sessionPresent(agent)
	return r0, nil
}

// StalledOnDialog is stalledOnDialog with a failed statement returned as the error.
func (e *Env) StalledOnDialog(q db.Q, agent, why string) (r0 bool, err error) {
	defer Catch(&err)
	r0 = e.stalledOnDialog(q, agent, why)
	return r0, nil
}

// ReclaimContext is reclaimContext with a failed statement returned as the error.
func (e *Env) ReclaimContext(q db.Q, agent string) (r0 bool, r1 string, err error) {
	defer Catch(&err)
	r0, r1 = e.reclaimContext(q, agent)
	return r0, r1, nil
}

// ClearOwed is clearOwed with a failed statement returned as the error.
func (e *Env) ClearOwed(q db.Q, agent string) (r0 int64, err error) {
	defer Catch(&err)
	r0 = e.clearOwed(q, agent)
	return r0, nil
}

// ClearHold is clearHold with a failed statement returned as the error.
func (e *Env) ClearHold(q db.Q, agent string) (r0 string, err error) {
	defer Catch(&err)
	r0 = e.clearHold(q, agent)
	return r0, nil
}

// TypingHold is typingHold with a failed statement returned as the error.
func (e *Env) TypingHold(q db.Q, agent string) (r0 string, err error) {
	defer Catch(&err)
	r0 = e.typingHold(q, agent)
	return r0, nil
}

// WallReason is wallReason with a failed statement returned as the error.
func (e *Env) WallReason(q db.Q, agent string, row *db.AgentState) (r0 string, err error) {
	defer Catch(&err)
	r0 = e.wallReason(q, agent, row)
	return r0, nil
}

// SessionChanged is sessionChanged with a failed statement returned as the error.
func (e *Env) SessionChanged(q db.Q, agent string) (r0 bool, err error) {
	defer Catch(&err)
	r0 = e.sessionChanged(q, agent)
	return r0, nil
}

// MarkTyped is markTyped with a failed statement returned as the error.
func (e *Env) MarkTyped(q db.Q, agent string, by map[string]any) (err error) {
	defer Catch(&err)
	e.markTyped(q, agent, by)
	return nil
}

// CheckHumanResolve is checkHumanResolve with a failed statement returned as the error.
func (e *Env) CheckHumanResolve(q db.Q, row *db.Request, tasks []int64, noTask *string, me string, oneTask bool) (r0 string, err error) {
	defer Catch(&err)
	r0 = e.checkHumanResolve(q, row, tasks, noTask, me, oneTask)
	return r0, nil
}

// RecordHumanResolve is recordHumanResolve with a failed statement returned as the error.
func (e *Env) RecordHumanResolve(q db.Q, rid int64, tasks []int64, noTask *string, me string) (err error) {
	defer Catch(&err)
	e.recordHumanResolve(q, rid, tasks, noTask, me)
	return nil
}

// LinkDelivery is linkDelivery with a failed statement returned as the error.
func (e *Env) LinkDelivery(q db.Q, tid int64, owner string, rid int64) (err error) {
	defer Catch(&err)
	e.linkDelivery(q, tid, owner, rid)
	return nil
}

// TypeSay is typeSay with a failed statement returned as the error.
func (e *Env) TypeSay(q db.Q, owner, body, why string) (err error) {
	defer Catch(&err)
	e.typeSay(q, owner, body, why)
	return nil
}

// AnswerVerb is answerVerb with a failed statement returned as the error.
func AnswerVerb(q db.Q, head *db.Request) (r0 string, err error) {
	defer Catch(&err)
	r0 = answerVerb(q, head)
	return r0, nil
}

// FoldedNotices is foldedNotices with a failed statement returned as the error.
func FoldedNotices(q db.Q, agent string) (r0 []*db.Request, err error) {
	defer Catch(&err)
	r0 = foldedNotices(q, agent)
	return r0, nil
}

// InReplyTo is inReplyTo with a failed statement returned as the error.
func InReplyTo(q db.Q, r *db.Request) (r0 string, err error) {
	defer Catch(&err)
	r0 = inReplyTo(q, r)
	return r0, nil
}

// TellText is tellText with a failed statement returned as the error.
func TellText(q db.Q, head *db.Request, depth int, aheadOf *db.Request, parked bool, folded []*db.Request) (r0 string, err error) {
	defer Catch(&err)
	r0 = tellText(q, head, depth, aheadOf, parked, folded)
	return r0, nil
}

// BatchText is batchText with a failed statement returned as the error.
func BatchText(q db.Q, batch []*db.Request, folded []*db.Request, aheadOf *db.Request) (r0 string, err error) {
	defer Catch(&err)
	r0 = batchText(q, batch, folded, aheadOf)
	return r0, nil
}

// PastDialog is pastDialog (delivery._past_dialog) with a failed statement returned as the error.
func (e *Env) PastDialog(q db.Q, agent string) (why string, err error) {
	defer Catch(&err)
	return e.pastDialog(q, agent, false), nil
}
