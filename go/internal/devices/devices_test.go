package devices

import (
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/rpc"
)

func gateOf(t *testing.T, err error) string {
	t.Helper()
	e, ok := err.(*rpc.Error)
	if !ok {
		t.Fatalf("not a refusal: %v", err)
	}
	return e.Data["gate"].(string)
}

func TestACodeRedeemsOnceAndTheStoreHoldsOnlyAHash(t *testing.T) {
	s := NewMemStore()
	m := New(s)
	code, _, _, uri := m.Pair(time.Minute)
	if !strings.Contains(uri, "code="+code) {
		t.Fatal(uri)
	}
	token, d, err := m.Redeem(code, "phone", "1.2.3.4")
	if err != nil || token == "" || d.Name != "phone" {
		t.Fatal(token, d, err)
	}
	if _, _, err := m.Redeem(code, "phone", "1.2.3.4"); gateOf(t, err) != "pairing_code" {
		t.Fatal(err)
	}
	if _, ok, _ := s.ByTokenHash(token); ok {
		t.Fatal("the store answers to the token itself")
	}
	if !m.Know(token) || m.Know(token+"x") || m.Know("") {
		t.Fatal("Know")
	}
	for h := range s.byHash {
		if strings.Contains(h, token) {
			t.Fatal("the token is in the store")
		}
	}
}

func TestACodeExpiresAndRefusesAsAWrongOneDoes(t *testing.T) {
	m := New(NewMemStore())
	now := time.Now()
	m.now = func() time.Time { return now }
	code, _, _, _ := m.Pair(5 * time.Second)
	now = now.Add(6 * time.Second)
	_, _, expired := m.Redeem(code, "p", "h")
	_, _, wrong := m.Redeem("WRONG-WRONG-WRONG", "p", "h")
	if gateOf(t, expired) != "pairing_code" || expired.Error() != wrong.Error() {
		t.Fatal(expired, wrong)
	}
}

func TestFiveWrongTriesKillACodeAndFiveWrongCodesLockAHostOut(t *testing.T) {
	m := New(NewMemStore())
	now := time.Now()
	m.now = func() time.Time { return now }
	code, _, _, _ := m.Pair(time.Minute)
	for i := 0; i < 5; i++ {
		m.Redeem("WRONG", "p", "10.0.0.1")
	}
	// the code died with the fifth wrong try, and that host is locked out
	if _, _, err := m.Redeem(code, "p", "10.0.0.2"); gateOf(t, err) != "pairing_code" {
		t.Fatal(err)
	}
	code, _, _, _ = m.Pair(time.Minute)
	if _, _, err := m.Redeem(code, "p", "10.0.0.1"); gateOf(t, err) != "attempt_limit" {
		t.Fatal(err)
	}
	now = now.Add(hostLock + time.Second)
	code, _, _, _ = m.Pair(time.Hour)
	if _, _, err := m.Redeem(code, "p", "10.0.0.1"); err != nil {
		t.Fatal(err)
	}
}

func TestWrongTriesSpreadOverTimeDoNotLockAHostOut(t *testing.T) {
	m := New(NewMemStore())
	now := time.Now()
	m.now = func() time.Time { return now }
	for i := 0; i < 20; i++ {
		now = now.Add(3 * time.Minute)
		m.Redeem("WRONG", "p", "h")
	}
	code, _, _, _ := m.Pair(time.Minute)
	if _, _, err := m.Redeem(code, "p", "h"); err != nil {
		t.Fatal(err)
	}
}

func TestRevokeClosesOnlyThatDevicesConnectionsAndTheTokenDies(t *testing.T) {
	m := New(NewMemStore())
	code, _, _, _ := m.Pair(time.Minute)
	token, d, _ := m.Redeem(code, "a", "h")
	code, _, _, _ = m.Pair(time.Minute)
	otherToken, other, _ := m.Redeem(code, "b", "h")
	var closed []string
	id, _, release, ok := m.Login(token, func() { closed = append(closed, "a1") })
	if !ok || id != d.ID {
		t.Fatal(id, ok)
	}
	defer release()
	m.Login(token, func() { closed = append(closed, "a2") })
	m.Login(otherToken, func() { closed = append(closed, "b") })
	if found, _ := m.Revoke(d.ID); !found {
		t.Fatal("not found")
	}
	if len(closed) != 2 || closed[0] == "b" || closed[1] == "b" {
		t.Fatal(closed)
	}
	if m.Know(token) || m.Live(d.ID) || !m.Live(other.ID) {
		t.Fatal("state after revoke")
	}
	if _, _, _, ok := m.Login(token, nil); ok {
		t.Fatal("a revoked token logged in")
	}
	if found, _ := m.Revoke(d.ID); found {
		t.Fatal("revoked twice")
	}
}

func TestAHostQuietForAWholeWindowIsForgotten(t *testing.T) {
	m := New(NewMemStore())
	now := time.Now()
	m.now = func() time.Time { return now }
	for _, h := range []string{"a", "b", "c"} {
		m.Redeem("WRONG", "p", h)
	}
	if len(m.wrongs) != 3 {
		t.Fatal(len(m.wrongs))
	}
	now = now.Add(hostWindow + time.Second)
	m.Redeem("WRONG", "p", "d")
	if len(m.wrongs) != 1 {
		t.Fatalf("%d hosts kept", len(m.wrongs))
	}
}

func TestADeviceAtItsCapLosesItsOldestConnectionsForTheNewOne(t *testing.T) {
	m := New(NewMemStore())
	m.maxConns = 3
	var logged []string
	m.Log = func(f string, a ...any) { logged = append(logged, fmt.Sprintf(f, a...)) }
	code, _, _, _ := m.Pair(time.Minute)
	token, d, _ := m.Redeem(code, "a", "h")
	code, _, _, _ = m.Pair(time.Minute)
	otherToken, other, _ := m.Redeem(code, "b", "h")
	var closed []int
	for i := 1; i <= 5; i++ {
		i := i
		if _, _, _, ok := m.Login(token, func() { closed = append(closed, i) }); !ok {
			t.Fatal("refused", i)
		}
	}
	if !reflect.DeepEqual(closed, []int{1, 2}) {
		t.Fatalf("closed %v, want the two oldest", closed)
	}
	if n := len(m.conns[d.ID]); n != 3 {
		t.Fatalf("%d open, want the cap", n)
	}
	if len(logged) != 2 || !strings.Contains(logged[0], "device a ") {
		t.Fatalf("each eviction is logged with the device's name: %q", logged)
	}
	// another device is not counted against it
	if _, _, _, ok := m.Login(otherToken, nil); !ok || len(m.conns[other.ID]) != 1 || len(closed) != 2 {
		t.Fatal("the cap is per device")
	}
}

func TestAClosedConnectionsReleaseDoesNotFreeAnotherOnesPlace(t *testing.T) {
	m := New(NewMemStore())
	m.maxConns = 2
	code, _, _, _ := m.Pair(time.Minute)
	token, d, _ := m.Redeem(code, "a", "h")
	_, _, release1, _ := m.Login(token, nil)
	m.Login(token, nil)
	m.Login(token, nil) // the first is evicted
	release1()          // its connection ends and releases late
	if n := len(m.conns[d.ID]); n != 2 {
		t.Fatalf("%d open after a late release of an evicted one, want 2", n)
	}
}
