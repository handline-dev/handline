package tick

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// loginWorld is a world whose account "acct" is stalled (or not) and whose agent's last turn did (or did not) end on a refused login.
func loginWorld(t *testing.T, stalled, failed bool) (*Tick, *world, *[]string) {
	failure := ""
	if failed {
		failure = "authentication_failed"
	}
	return failureWorld(t, stalled, failure, 1)
}

// failureWorld is loginWorld with the agent's last turn ended on failure ("" for none), seen repeats times on the account.
func failureWorld(t *testing.T, stalled bool, failure string, repeats int) (*Tick, *world, *[]string) {
	tk, w := newWorld(t)
	setState(t, tk, "a", states.Idle, 0)
	w.reading = &AccountReading{Account: "acct", Source: "recorded"}
	var stalls []string
	tk.Login = Login{
		Stalled: func(db.Q, string) (string, string, bool) { return "was stalled", "", stalled },
		Failed: func(db.Q, string, string) (string, string, int64, bool) {
			return failure, "401", 1000, failure != ""
		},
		Repeats: func(_ db.Q, _, errType string) int {
			if errType != failure {
				t.Fatalf("counted %s", errType)
			}
			return repeats
		},
		Stall: func(_ db.Q, agent, acct, errType, why string, _ int64) bool {
			stalls = append(stalls, acct+" "+errType+" "+why)
			return true
		},
	}
	return tk, w, &stalls
}

func TestAnAuthFailedTurnStallsTheAccountAndMovesTheAgentOff(t *testing.T) {
	tk, w, stalls := loginWorld(t, false, true)
	w.movePlan, w.performOK = "other", true
	actions := supervise(tk, "a")
	if len(*stalls) != 1 || !w.did("handoff a login") || !has(actions, "a: handoff (login)") || has(actions, "degraded") {
		t.Fatalf("%v %v %v", *stalls, w.calls, actions)
	}
}

func TestAStalledAccountMovesItsAgentsWithoutAnotherSighting(t *testing.T) {
	tk, w, stalls := loginWorld(t, true, false)
	w.movePlan, w.performOK = "other", true
	if actions := supervise(tk, "a"); len(*stalls) != 0 || !w.did("handoff a login") || !has(actions, "handoff (login)") {
		t.Fatalf("%v %v %v", *stalls, w.calls, actions)
	}
}

func TestWithNowhereToMoveTheAgentIsStalledForAPerson(t *testing.T) {
	tk, w, _ := loginWorld(t, true, false)
	w.movePlan = ""
	actions := supervise(tk, "a")
	row, _ := db.GetAgentState(tk.Q, "a")
	if w.did("handoff a login") || !has(actions, "a: STALLED, login expired on acct") || row.State != states.Stalled || row.Detail.String == "" {
		t.Fatalf("%v %v %+v", w.calls, actions, row)
	}
}

func TestAnAccountNobodyStalledAndNoRefusedTurnLeavesTheAgentAlone(t *testing.T) {
	tk, w, stalls := loginWorld(t, false, false)
	w.movePlan = "other"
	if actions := supervise(tk, "a"); len(*stalls) != 0 || w.did("handoff a login") || has(actions, "login") {
		t.Fatalf("%v %v %v", *stalls, w.calls, actions)
	}
}

func TestAnAgentOnNoKnownAccountIsNotJudgedForItsLogin(t *testing.T) {
	tk, w, stalls := loginWorld(t, true, true)
	w.reading = nil
	w.movePlan = "other"
	if supervise(tk, "a"); len(*stalls) != 0 || w.did("handoff a login") {
		t.Fatalf("%v %v", *stalls, w.calls)
	}
}

func TestAnAgentWithNoSessionOnAStalledAccountIsLeftToTheStartPath(t *testing.T) {
	tk, w, stalls := loginWorld(t, true, true)
	w.alive["a"], w.movePlan, w.performOK = false, "other", true
	if actions := supervise(tk, "a"); len(*stalls) != 0 || w.did("handoff a login") || has(actions, "login") {
		t.Fatalf("a handoff has nothing to end, and it would return before the start: %v %v %v", *stalls, w.calls, actions)
	}
}

func TestAnOrgThatRefusesOAuthStallsTheAccountAtOnceAndMovesTheAgentOff(t *testing.T) {
	tk, w, stalls := failureWorld(t, false, "oauth_org_not_allowed", 1)
	w.movePlan, w.performOK = "other", true
	actions := supervise(tk, "a")
	if len(*stalls) != 1 || !strings.Contains((*stalls)[0], "acct oauth_org_not_allowed a's turn ended on oauth_org_not_allowed") ||
		!w.did("handoff a login") || !has(actions, "a: handoff (login)") {
		t.Fatalf("%v %v %v", *stalls, w.calls, actions)
	}
}

func TestErrorsThatAreNotTheAccountsNeverStallIt(t *testing.T) {
	for _, e := range []string{"rate_limit", "server_error", "overloaded", "invalid_request", "max_output_tokens"} {
		tk, w, stalls := failureWorld(t, false, e, 50)
		w.movePlan, w.performOK = "other", true
		if actions := supervise(tk, "a"); len(*stalls) != 0 || w.did("handoff a login") || has(actions, "login") {
			t.Errorf("%s: %v %v %v", e, *stalls, w.calls, actions)
		}
	}
}

func TestAnUnseenErrorOnlyRetriesUntilItRepeats(t *testing.T) {
	tk, w, stalls := failureWorld(t, false, "brand_new_error", RepeatN-1)
	w.movePlan, w.performOK = "other", true
	if actions := supervise(tk, "a"); len(*stalls) != 0 || w.did("handoff a login") || has(actions, "login") {
		t.Fatalf("below the repeat count: %v %v %v", *stalls, w.calls, actions)
	}
	tk, w, stalls = failureWorld(t, false, "brand_new_error", RepeatN)
	w.movePlan, w.performOK = "other", true
	actions := supervise(tk, "a")
	if len(*stalls) != 1 || !strings.Contains((*stalls)[0], "4 times on acct") || !w.did("handoff a login") || !has(actions, "a: handoff (login)") {
		t.Fatalf("%v %v %v", *stalls, w.calls, actions)
	}
}

func TestAStallNamesTheErrorAndBothWaysOut(t *testing.T) {
	tk, w, _ := failureWorld(t, false, "account_on_hold", 1)
	w.movePlan = ""
	actions := supervise(tk, "a")
	row, _ := db.GetAgentState(tk.Q, "a")
	if !has(actions, "a: STALLED, account_on_hold on acct") || !strings.Contains(row.Detail.String, LoginStall) ||
		!strings.Contains(row.Detail.String, "log acct in, or wake it") {
		t.Fatalf("%v %+v", actions, row)
	}
}

// endedWorld is a world whose account "acct" still answers but whose subscription has ended (or not), with what the founder is told.
func endedWorld(t *testing.T, ended bool) (*Tick, *world, *[]string) {
	tk, w, stalls := loginWorld(t, false, false)
	tk.Login.Ended = func(string) string {
		if ended {
			return "subscription ended 2026-10-21 (month cycle)"
		}
		return ""
	}
	var told []string
	tk.Login.NoRoom = func(_ db.Q, agent, acct, key, text string) { told = append(told, acct+" | "+key+" | "+text) }
	if len(*stalls) != 0 {
		t.Fatal(*stalls)
	}
	return tk, w, &told
}

func TestAnAgentLeavesAnAccountWhoseEndHasPassedThoughItStillAnswers(t *testing.T) {
	tk, w, told := endedWorld(t, true)
	w.movePlan, w.performOK = "other", true
	actions := supervise(tk, "a")
	if !w.did("handoff a login") || !has(actions, "a: handoff (login)") || len(*told) != 0 {
		t.Fatalf("%v %v %v", w.calls, actions, *told)
	}
}

func TestBeforeItsEndAnAccountKeepsItsAgents(t *testing.T) {
	tk, w, told := endedWorld(t, false)
	w.movePlan, w.performOK = "other", true
	if actions := supervise(tk, "a"); w.did("handoff a login") || has(actions, "login") || len(*told) != 0 {
		t.Fatalf("%v %v %v", w.calls, actions, *told)
	}
}

func TestWithNoRoomAnEndedAccountKeepsItsAgentsAndTheFounderIsTold(t *testing.T) {
	tk, w, told := endedWorld(t, true)
	w.movePlan = ""
	actions := supervise(tk, "a")
	row, _ := db.GetAgentState(tk.Q, "a")
	if w.did("handoff a login") || has(actions, "STALLED") || row.State == states.Stalled {
		t.Fatalf("an agent on an account that still answers is not stopped for want of another: %v %v %+v", w.calls, actions, row)
	}
	if len(*told) != 1 || !strings.HasPrefix((*told)[0], "acct | subscription ended 2026-10-21 (month cycle) | acct's subscription has ended") {
		t.Fatalf("%q", *told)
	}
}

func TestWithNoRoomARefusingAccountStallsItsAgentsAndTheFounderIsTold(t *testing.T) {
	tk, w, told := endedWorld(t, false)
	tk.Login.Stalled = func(db.Q, string) (string, string, bool) {
		return "a's turn ended on billing_error", "billing_error", true
	}
	w.movePlan = ""
	actions := supervise(tk, "a")
	if !has(actions, "a: STALLED, billing_error on acct") || len(*told) != 1 ||
		!strings.HasPrefix((*told)[0], "acct | a's turn ended on billing_error | acct cannot be used (billing_error)") {
		t.Fatalf("%v %q", actions, *told)
	}
}
