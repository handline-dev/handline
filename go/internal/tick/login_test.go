package tick

import (
	"testing"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/states"
)

// loginWorld is a world whose account "acct" is marked (or not) and whose agent's last turn did (or did not) end on a refused login.
func loginWorld(t *testing.T, marked, failed bool) (*Tick, *world, *[]string) {
	tk, w := newWorld(t)
	setState(t, tk, "a", states.Idle, 0)
	w.reading = &AccountReading{Account: "acct", Source: "recorded"}
	var marks []string
	tk.Login = Login{
		Expired:    func(string) (string, bool) { return "was marked", marked },
		AuthFailed: func(db.Q, string, string) (string, bool) { return ": 401", failed },
		Mark:       func(_ db.Q, agent, acct, why string) bool { marks = append(marks, acct+" "+why); return true },
	}
	return tk, w, &marks
}

func TestAnAuthFailedTurnMarksTheAccountAndMovesTheAgentOff(t *testing.T) {
	tk, w, marks := loginWorld(t, false, true)
	w.movePlan, w.performOK = "other", true
	actions := supervise(tk, "a")
	if len(*marks) != 1 || !w.did("handoff a login") || !has(actions, "a: handoff (login)") || has(actions, "degraded") {
		t.Fatalf("%v %v %v", *marks, w.calls, actions)
	}
}

func TestAMarkedAccountMovesItsAgentsWithoutAnotherSighting(t *testing.T) {
	tk, w, marks := loginWorld(t, true, false)
	w.movePlan, w.performOK = "other", true
	if actions := supervise(tk, "a"); len(*marks) != 0 || !w.did("handoff a login") || !has(actions, "handoff (login)") {
		t.Fatalf("%v %v %v", *marks, w.calls, actions)
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

func TestAnAccountNobodyMarkedAndNoRefusedTurnLeavesTheAgentAlone(t *testing.T) {
	tk, w, marks := loginWorld(t, false, false)
	w.movePlan = "other"
	if actions := supervise(tk, "a"); len(*marks) != 0 || w.did("handoff a login") || has(actions, "login") {
		t.Fatalf("%v %v %v", *marks, w.calls, actions)
	}
}

func TestAnAgentOnNoKnownAccountIsNotJudgedForItsLogin(t *testing.T) {
	tk, w, marks := loginWorld(t, true, true)
	w.reading = nil
	w.movePlan = "other"
	if supervise(tk, "a"); len(*marks) != 0 || w.did("handoff a login") {
		t.Fatalf("%v %v", *marks, w.calls)
	}
}

func TestAnAgentWithNoSessionOnAMarkedAccountIsLeftToTheStartPath(t *testing.T) {
	tk, w, marks := loginWorld(t, true, true)
	w.alive["a"], w.movePlan, w.performOK = false, "other", true
	if actions := supervise(tk, "a"); len(*marks) != 0 || w.did("handoff a login") || has(actions, "login") {
		t.Fatalf("a handoff has nothing to end, and it would return before the start: %v %v %v", *marks, w.calls, actions)
	}
}
