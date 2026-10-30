package devices

import (
	"path/filepath"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
)

// A push token is kept on the device's row and listed as push; a new one replaces it; revoking the device drops it with the row.
func TestAPushTokenIsKeptOnTheRowAndGoesWithIt(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	m := New(SQLStore{DB: db})
	code, _, _, _ := m.Pair(DefaultTTL)
	token, d, err := m.Redeem(code, "phone", "")
	if err != nil {
		t.Fatal(err)
	}
	push := func() any {
		t.Helper()
		rows, err := m.List()
		if err != nil || len(rows) != 1 {
			t.Fatal(rows, err)
		}
		return rows[0]["push"]
	}
	if push() != false {
		t.Fatal("a device that gave no token has push")
	}
	if found, err := m.SetPush(d.ID, Push{Platform: "fcm", Token: "old"}); !found || err != nil {
		t.Fatal(found, err)
	}
	if got, _, _ := (SQLStore{DB: db}).ByTokenHash(hashOf(token)); got.Push == nil || *got.Push != (Push{Platform: "fcm", Token: "old"}) {
		t.Fatalf("the token is read back with its platform: %+v", got.Push)
	}
	if found, err := m.SetPush(d.ID, Push{Platform: "apns", Token: "new", Sandbox: true}); !found || err != nil {
		t.Fatal(found, err)
	}
	if push() != true {
		t.Fatal("a device that gave a token has no push")
	}
	got, _, _ := SQLStore{DB: db}.ByTokenHash(hashOf(token))
	if got.Push == nil || *got.Push != (Push{Platform: "apns", Token: "new", Sandbox: true}) {
		t.Fatalf("the newest token replaces the one before: %+v", got.Push)
	}
	if found, err := m.SetPush("dev_gone", Push{Platform: "fcm", Token: "x"}); found || err != nil {
		t.Fatal("a device that is not paired keeps no token", found, err)
	}
	if _, err := m.Revoke(d.ID); err != nil {
		t.Fatal(err)
	}
	var left int
	if err := db.QueryRow("SELECT count(*) FROM devices WHERE push_token IS NOT NULL").Scan(&left); err != nil || left != 0 {
		t.Fatal("a revoked device's token is gone", left, err)
	}
}

// The memory store keeps a push token as the database does: on a paired device, replaced by the next, and refused for one not paired.
func TestTheMemStoreKeepsAPushTokenOnAPairedDeviceOnly(t *testing.T) {
	s := NewMemStore()
	m := New(s)
	code, _, _, _ := m.Pair(DefaultTTL)
	token, d, err := m.Redeem(code, "phone", "")
	if err != nil {
		t.Fatal(err)
	}
	m.SetPush(d.ID, Push{Platform: "apns", Token: "old", Sandbox: true})
	if found, err := m.SetPush(d.ID, Push{Platform: "fcm", Token: "new"}); !found || err != nil {
		t.Fatal(found, err)
	}
	if got, _, _ := s.ByTokenHash(hashOf(token)); got.Push == nil || *got.Push != (Push{Platform: "fcm", Token: "new"}) {
		t.Fatalf("the newest token replaces the one before: %+v", got.Push)
	}
	if found, err := m.SetPush("dev_gone", Push{Platform: "fcm", Token: "x"}); found || err != nil {
		t.Fatal("a device that is not paired keeps no token", found, err)
	}
}

// A device revoked while its connection is open has no row: its push token is not_found, not accepted.
func TestAPushTokenFromADeviceNotPairedIsNotFound(t *testing.T) {
	s := rpc.NewServer(nil, "x", store.LatestVersion(), "local", func(string, ...any) {})
	Register(s, New(NewMemStore()), nil)
	var call func(*rpc.Conn, map[string]any) (any, error)
	s.Wrap("device.push_token", func(next func(*rpc.Conn, map[string]any) (any, error)) func(*rpc.Conn, map[string]any) (any, error) {
		call = next
		return next
	})
	c := &rpc.Conn{Caller: rpc.Caller{Role: "founder", Info: map[string]any{"kind": "device", "device": "dev_gone"}}}
	_, err := call(c, map[string]any{"platform": "fcm", "token": "x"})
	if e, ok := err.(*rpc.Error); !ok || e.Code != "not_found" {
		t.Fatalf("got %v", err)
	}
}

// A token Apple or Google no longer takes is dropped, but only while it is still the device's: one the device gave since stays.
func TestDroppingAPushTokenSparesANewerOne(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for name, s := range map[string]Store{"sql": SQLStore{DB: db}, "mem": NewMemStore()} {
		t.Run(name, func(t *testing.T) {
			m := New(s)
			code, _, _, _ := m.Pair(DefaultTTL)
			token, d, err := m.Redeem(code, "phone", "")
			if err != nil {
				t.Fatal(err)
			}
			pushOf := func() *Push {
				got, _, _ := s.ByTokenHash(hashOf(token))
				return got.Push
			}
			m.SetPush(d.ID, Push{Platform: "apns", Token: "new", Sandbox: true})
			if err := s.DropPush(d.ID, "old"); err != nil || pushOf() == nil {
				t.Fatalf("a stale token's drop took the newer one: %v %+v", err, pushOf())
			}
			if err := s.DropPush(d.ID, "new"); err != nil || pushOf() != nil {
				t.Fatalf("the token is still kept: %v %+v", err, pushOf())
			}
		})
	}
}
