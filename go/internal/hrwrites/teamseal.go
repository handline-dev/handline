package hrwrites

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/sealed"
)

// sealWords is how a file took on is described: the words the Team page shows.
func sealWords(kind string) string {
	if kind == "new" {
		return "no seal before"
	}
	return "it did not match its seal"
}

// teamSealMethod is team.seal (cmd_team_seal in cli/team.py): seal team.yaml and every agent.yaml as they are now. With dry_run it answers what would be
// sealed, and the digests, and writes nothing; the call without it is the go-ahead.
func (e *Env) teamSealMethod(c *rpc.Conn, p map[string]any) (any, error) {
	dry := false
	if v := p["dry_run"]; v != nil {
		var ok bool
		if dry, ok = v.(bool); !ok {
			return nil, rpc.BadRequestError("dry_run is true or false", "dry_run", rpc.InvalidParams)
		}
	}
	seals := sealed.New(e.Home)
	if dry {
		plan, err := seals.Plan()
		if err != nil {
			return nil, err
		}
		if len(plan) == 0 {
			return map[string]any{"ok": true, "plan": "nothing to seal: every file already matches its seal"}, nil
		}
		var b strings.Builder
		for _, a := range plan {
			fmt.Fprintf(&b, "  seal %s (%s) as %s\n", a.Label, sealWords(a.Kind), a.Digest)
		}
		fmt.Fprintf(&b, "%d file(s) would be sealed as they are now", len(plan))
		return map[string]any{"ok": true, "plan": b.String()}, nil
	}
	adopted, err := seals.SealEverything()
	if err != nil {
		return nil, err
	}
	if key, ok := p["request_id"].(string); ok {
		if err := c.RecordWrite("team.seal", key); err != nil {
			return nil, err
		}
	}
	if len(adopted) == 0 {
		return map[string]any{"ok": true, "said": "nothing to seal: every file already matches its seal"}, nil
	}
	var b strings.Builder
	for _, a := range adopted {
		fmt.Fprintf(&b, "  sealed %s (%s)\n", a.Label, sealWords(a.Kind))
	}
	fmt.Fprintf(&b, "sealed %d file(s) as they are now", len(adopted))
	return map[string]any{"ok": true, "said": b.String()}, nil
}
