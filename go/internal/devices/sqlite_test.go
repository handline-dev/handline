package devices

import (
	"path/filepath"
	"testing"

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
	if found, err := m.SetPush(d.ID, Push{Platform: "apns", Token: "old"}); !found || err != nil {
		t.Fatal(found, err)
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
