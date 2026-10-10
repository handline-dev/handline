package compose

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"github.com/handline/handline/go/internal/db"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/supervisor"
	"github.com/handline/handline/go/internal/taskwrites"
	"github.com/handline/handline/go/internal/team"
)

// authStatusTimeout is how long `claude auth status` may take (the Python vendor's ten seconds).
const authStatusTimeout = 10 * time.Second

// founderAsker is subscriptions.Asker: a decision for the founder, opened by agent.
type founderAsker struct{ env *supervisor.Env }

func (a founderAsker) RaiseDecision(q db.Q, agent, text, reason string) (int64, error) {
	var after supervisor.After
	rid, err := a.env.RaiseDecision(q, &after, agent, text, reason, "", "", 0, 0)
	if err != nil {
		return 0, err
	}
	after.Run()
	return rid, nil
}

// founderCloser is subscriptions.Tasks: a task marked done as the founder's own card, then told as any done is.
type founderCloser struct {
	env  *supervisor.Env
	home string
}

func (c founderCloser) MarkDoneByFounder(_ db.Q, tid int64, reason string) (bool, string, error) {
	tx, err := c.env.DB.Begin()
	if err != nil {
		return false, "", err
	}
	res, err := taskwrites.MarkDone(tx, c.home, db.Human, taskwrites.DoneIn{Task: tid, Words: reason})
	if err != nil {
		tx.Rollback()
		var re *rpc.Error
		if errors.As(err, &re) {
			return false, re.Message, nil
		}
		return false, "", err
	}
	if err := tx.Commit(); err != nil {
		return false, "", err
	}
	c.env.Done(tid, db.Human, res.Owner, res.Told, res.Forget, res.Finished)
	return true, res.Said, nil
}

// vendorPlans is subscriptions.Plans: the plan on disk, and `claude auth status --json` for the one asked again.
type vendorPlans struct{ home string }

func (p vendorPlans) Recorded(acct string) (string, bool) {
	return team.Accounts{Home: p.home}.RecordedSubscriptionType(acct)
}

// Refresh is accounts.subscription_type(refresh=True): a reading that cannot be taken leaves what is recorded.
func (p vendorPlans) Refresh(acct string) (string, error) {
	recorded, _ := p.Recorded(acct)
	if !(team.Accounts{Home: p.home}).LoggedIn(acct) || team.AccountVendor(p.home, acct) != team.DefaultVendor {
		return recorded, nil
	}
	dir, err := team.AccountConfigDir(p.home, acct)
	if err != nil {
		return recorded, nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), authStatusTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "claude", "auth", "status", "--json")
	cmd.Env = append(os.Environ(), team.ConfigDirEnv(team.DefaultVendor)+"="+dir)
	out, err := cmd.Output()
	var data struct {
		Sub string `json:"subscriptionType"`
	}
	if err != nil || json.Unmarshal(out, &data) != nil || data.Sub == "" {
		return recorded, nil
	}
	f := filepath.Join(paths.AccountDir(p.home, acct), "subscription.json")
	rec, _ := json.Marshal(map[string]any{"subscriptionType": data.Sub, "recorded_at": time.Now().Unix()})
	if err := os.MkdirAll(filepath.Dir(f), 0o755); err != nil {
		return "", err
	}
	return data.Sub, os.WriteFile(f, rec, 0o644)
}
