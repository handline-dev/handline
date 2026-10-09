package compose

import (
	"encoding/json"
	"os"
	"path/filepath"

	"github.com/handline/handline/go/internal/session"
	"github.com/handline/handline/go/internal/structured"
)

// SharedPolicy is the permission policy of every structured agent: the shell guard, and the permissions.allow rules of the shared
// settings, which the session itself does not load (it reads project settings and the hooks only). The file is read at each call, so a
// rule added there holds from the next tool call. Settings that cannot be read allow nothing: a person is asked.
func SharedPolicy(home string) func(agent string) structured.Policy {
	return func(string) structured.Policy {
		p := structured.Policy{Guard: session.Guard}
		b, err := os.ReadFile(filepath.Join(home, "shared", ".claude", "settings.json"))
		if err != nil {
			return p
		}
		var s struct {
			Permissions struct {
				Allow []string `json:"allow"`
			} `json:"permissions"`
		}
		if json.Unmarshal(b, &s) == nil {
			p.Allow = s.Permissions.Allow
		}
		return p
	}
}
