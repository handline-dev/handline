package adminwrites

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/reads"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/team"
)

// auth makes every call the founder's, or the agent as names while it is set.
type auth struct{ as *string }

func (a auth) AgentOf(int) string {
	if a.as == nil {
		return ""
	}
	return *a.as
}
func (auth) FounderKeyOK(k string) bool { return k == "founder" }

type rig struct {
	t    *testing.T
	home string
	srv  *rpc.Server
	e    *Env
	as   string
}

func newRig(t *testing.T) *rig {
	t.Helper()
	t.Setenv("HANDLINE_TEST_ROOT", t.TempDir())
	for _, k := range []string{"OCP_AGENT", "HANDLINE_AGENT"} {
		t.Setenv(k, "")
	}
	home := t.TempDir()
	for _, a := range []string{"alice", "bob"} {
		os.MkdirAll(filepath.Join(home, "agents", a), 0o755)
		os.WriteFile(filepath.Join(home, "agents", a, "agent.yaml"), []byte("vendor: claude\n"), 0o644)
	}
	os.MkdirAll(filepath.Join(home, "accounts", "main"), 0o755)
	d, err := store.Open(filepath.Join(home, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { d.Close() })
	e := &Env{DB: d, Home: home}
	r := &rig{t: t, home: home, e: e}
	s := rpc.NewServer(auth{&r.as}, "x", store.LatestVersion(), "local", func(string, ...any) {})
	s.DB = d
	Register(s, e)
	r.srv = s
	return r
}

func (r *rig) one(method, params string) map[string]any {
	r.t.Helper()
	cl, sv := net.Pipe()
	done := make(chan struct{})
	go func() { r.srv.Serve(rpc.NewLineTransport(sv), 0); sv.Close(); close(done) }()
	defer func() { cl.Close(); <-done }()
	rd := bufio.NewReader(cl)
	cl.SetDeadline(time.Now().Add(10 * time.Second))
	var m map[string]any
	for _, line := range []string{`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1,"key":"founder"}}`,
		fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":%q,"params":%s}`, method, params)} {
		cl.Write([]byte(line + "\n"))
		b, err := rd.ReadBytes('\n')
		if err != nil {
			r.t.Fatal(err)
		}
		m = nil
		json.Unmarshal(b, &m)
	}
	return m
}

func (r *rig) result(m map[string]any) map[string]any {
	r.t.Helper()
	res, ok := m["result"].(map[string]any)
	if !ok {
		r.t.Fatalf("not a result: %v", m)
	}
	return res
}

func (r *rig) code(m map[string]any) string {
	r.t.Helper()
	e, ok := m["error"].(map[string]any)
	if !ok {
		r.t.Fatalf("not an error: %v", m)
	}
	return e["data"].(map[string]any)["code"].(string)
}

func (r *rig) file(parts ...string) string {
	b, _ := os.ReadFile(filepath.Join(append([]string{r.home}, parts...)...))
	return string(b)
}

func TestSchedulesAreAddedEditedMovedAndRemovedByTheirId(t *testing.T) {
	r := newRig(t)
	add := `{"target":"alice","cron":"0  9 * * *","message":"morning   check","request_id":"s1"}`
	res := r.result(r.one("schedule.add", add))
	id := reads.ScheduleID("alice", reads.Schedule{Cron: "0 9 * * *", Message: "morning check"})
	if res["schedule"] != float64(id) || res["said"] != "alice: scheduled 0 9 * * *" {
		t.Fatalf("%v", res)
	}
	if r.result(r.one("schedule.add", add))["replayed"] != true || strings.Count(r.file("agents", "alice", "agent.yaml"), "morning check") != 1 {
		t.Fatal("a retry adds no second schedule")
	}
	if r.code(r.one("schedule.add", `{"target":"alice","cron":"0 9 * * *","message":"morning check","request_id":"s2"}`)) != "refused" {
		t.Fatal("the same schedule twice is refused")
	}
	if r.code(r.one("schedule.add", `{"target":"alice","cron":"nope","message":"x","request_id":"s3"}`)) != "bad_request" {
		t.Fatal("a bad cron is bad_request")
	}
	moved := r.result(r.one("schedule.edit", fmt.Sprintf(`{"schedule":%d,"target":"bob","cron":"30 9 * * 1-5","request_id":"s4"}`, id)))
	idBob := reads.ScheduleID("bob", reads.Schedule{Cron: "30 9 * * 1-5", Message: "morning check"})
	if moved["schedule"] != float64(idBob) || strings.Contains(r.file("agents", "alice", "agent.yaml"), "schedules") || !strings.Contains(r.file("agents", "bob", "agent.yaml"), "30 9 * * 1-5") {
		t.Fatalf("edited and moved to bob: %v\n%s", moved, r.file("agents", "bob", "agent.yaml"))
	}
	r.result(r.one("schedule.remove", fmt.Sprintf(`{"schedule":%d,"request_id":"s5"}`, idBob)))
	if strings.Contains(r.file("agents", "bob", "agent.yaml"), "schedules") {
		t.Fatal("removed")
	}
	if r.code(r.one("schedule.remove", fmt.Sprintf(`{"schedule":%d,"request_id":"s6"}`, idBob))) != "not_found" {
		t.Fatal("a removed schedule is not found")
	}
}

func TestSettingsSetWritesEveryKeyOrNoneAndClearsOnNull(t *testing.T) {
	r := newRig(t)
	os.WriteFile(filepath.Join(r.home, "config.yaml"), []byte("active_cap: 3\ndev_mode: true\n"), 0o644)
	res := r.result(r.one("settings.set", `{"changes":{"bg_notice_s":120,"dev_mode":null},"request_id":"t1"}`))
	if res["said"] != "bg_notice_s set, dev_mode cleared" {
		t.Fatalf("%v", res)
	}
	if got := r.file("config.yaml"); !strings.Contains(got, "active_cap: 3") || !strings.Contains(got, "bg_notice_s: 120") || strings.Contains(got, "dev_mode") {
		t.Fatalf("the other settings stay: %q", got)
	}
	before := r.file("config.yaml")
	if r.code(r.one("settings.set", `{"changes":{"bg_notice_s":5,"active_cap":0},"request_id":"t2"}`)) != "bad_request" || r.file("config.yaml") != before {
		t.Fatal("nothing is written if any key is refused")
	}
	if r.code(r.one("settings.set", `{"changes":{"active_cap":2},"agent":"alice","request_id":"t3"}`)) != "bad_request" {
		t.Fatal("active_cap has no per-agent value")
	}
	r.result(r.one("settings.set", `{"changes":{"park_timeout_s":60},"agent":"alice","request_id":"t4"}`))
	if !strings.Contains(r.file("agents", "alice", "agent.yaml"), "park_timeout_s: 60") {
		t.Fatal("the agent's own file")
	}
}

func TestTheLoadLimitsAreWholeNumbersForTheWholeMachine(t *testing.T) {
	r := newRig(t)
	r.result(r.one("settings.set", `{"changes":{"host_active_cap":0,"load_per_core_pct":200,"ram_free_min_pct":100},"request_id":"l1"}`))
	if got := r.file("config.yaml"); !strings.Contains(got, "host_active_cap: 0") || !strings.Contains(got, "load_per_core_pct: 200") ||
		!strings.Contains(got, "ram_free_min_pct: 100") {
		t.Fatalf("%q", got)
	}
	for i, bad := range []string{`{"ram_free_min_pct":101}`, `{"host_active_cap":-1}`, `{"load_per_core_pct":1.5}`} {
		if r.code(r.one("settings.set", `{"changes":`+bad+`,"request_id":"l`+strconv.Itoa(i+2)+`"}`)) != "bad_request" {
			t.Fatalf("%s was taken", bad)
		}
	}
	if r.code(r.one("settings.set", `{"changes":{"host_active_cap":2},"agent":"alice","request_id":"l9"}`)) != "bad_request" {
		t.Fatal("a load limit has no per-agent value")
	}
}

func TestEveryPushSwitchIsAFounderFlagForTheWholeTeam(t *testing.T) {
	for _, key := range []string{"push_prompt_asked", "push_task_done", "push_stall", "push_account_out"} {
		r := newRig(t)
		r.result(r.one("settings.set", `{"changes":{"`+key+`":false},"request_id":"p1"}`))
		if !strings.Contains(r.file("config.yaml"), key+": false") {
			t.Fatalf("%s: %q", key, r.file("config.yaml"))
		}
		if r.code(r.one("settings.set", `{"changes":{"`+key+`":"no"},"request_id":"p2"}`)) != "bad_request" {
			t.Fatalf("%s: a push switch is true or false", key)
		}
		if r.code(r.one("settings.set", `{"changes":{"`+key+`":false},"agent":"alice","request_id":"p3"}`)) != "bad_request" {
			t.Fatalf("%s: a push switch has no per-agent value", key)
		}
		r.result(r.one("settings.set", `{"changes":{"`+key+`":null},"request_id":"p4"}`))
		if strings.Contains(r.file("config.yaml"), key) {
			t.Fatalf("%s: null clears it back to the default", key)
		}
	}
}

func TestTheHandoffPromptIsOneLineOfTextGloballyOrForOneAgent(t *testing.T) {
	r := newRig(t)
	r.result(r.one("settings.set", `{"changes":{"handoff_prompt":"Write {path}: what is next, 100%."},"request_id":"h1"}`))
	if got := r.file("config.yaml"); !strings.Contains(got, `handoff_prompt: "Write {path}: what is next, 100%."`) {
		t.Fatalf("%q", got)
	}
	r.result(r.one("settings.set", `{"changes":{"handoff_prompt":"alice: {path}, what is next"},"agent":"alice","request_id":"h2"}`))
	if !strings.Contains(r.file("agents", "alice", "agent.yaml"), "handoff_prompt:") {
		t.Fatal("the agent's own file")
	}
	for i, bad := range []string{`"two\nlines"`, `"` + strings.Repeat("x", LineMax+1) + `"`, `7`} {
		if r.code(r.one("settings.set", `{"changes":{"handoff_prompt":`+bad+`},"request_id":"hb`+strconv.Itoa(i)+`"}`)) != "bad_request" {
			t.Fatalf("accepted %.20s", bad)
		}
	}
}

func TestAccountSetWritesConfigAndRefusesWhatNeedsTheProfile(t *testing.T) {
	r := newRig(t)
	if r.code(r.one("account.set", `{"account":"nobody","cap":2,"request_id":"a1"}`)) != "not_found" {
		t.Fatal("no such account")
	}
	res := r.result(r.one("account.set", `{"account":"main","label":"Work","cap":4,"recover":false,"cycle_end":"2030-01-31","request_id":"a3"}`))
	if res["said"] != "alias Work; main: at most 4 agents mid-turn; usage recovery off; main: last day 2030-01-31" {
		t.Fatalf("%v", res)
	}
	got := r.file("config.yaml")
	for _, want := range []string{"label_main: Work", "active_cap_main: 4", "usage_recover_main: false", "month_cycle_main: true", "sub_end_main"} {
		if !strings.Contains(got, want) {
			t.Fatalf("%s missing from %q", want, got)
		}
	}
	if r.result(r.one("account.set", `{"account":"main","cap":4,"request_id":"a4"}`))["said"] != "nothing changed" {
		t.Fatal("what is already so changes nothing")
	}
	os.Remove(filepath.Join(r.home, "config.yaml"))
	if r.code(r.one("account.set", `{"account":"main","cycle":true,"request_id":"a5"}`)) != "refused" || r.file("config.yaml") != "" {
		t.Fatal("the cycle on with no end needs the profile: refused, nothing written")
	}
}

func (auth) SessionsReadable() error { return nil }

func TestAccountWakeEndsTheStallAndSaysTheFailuresBeforeItAreOld(t *testing.T) {
	r := newRig(t)
	dir := filepath.Join(r.home, "accounts", "main", ".claude")
	os.MkdirAll(dir, 0o755)
	cred := filepath.Join(dir, ".credentials.json")
	os.WriteFile(cred, []byte("c"), 0o600)
	old := time.Now().Add(-time.Hour)
	os.Chtimes(cred, old, old)
	accounts := team.Accounts{Home: r.home}
	if r.code(r.one("account.wake", `{"account":"main","request_id":"u1"}`)) != "refused" {
		t.Fatal("an account that is not stalled has nothing to wake")
	}
	if _, err := accounts.StallAccount("main", "oauth_org_not_allowed", "refused", time.Now()); err != nil {
		t.Fatal(err)
	}
	if res := r.result(r.one("account.wake", `{"account":"main","request_id":"u2"}`)); res["said"] != "main: STALLED -> usable" {
		t.Fatalf("%v", res)
	}
	var n int
	r.e.DB.QueryRow("SELECT COUNT(*) FROM events WHERE kind = 'account_woken' AND account = 'main'").Scan(&n)
	if accounts.Stalled("main") != nil || n != 1 {
		t.Fatalf("still stalled, or %d account_woken events", n)
	}
}
