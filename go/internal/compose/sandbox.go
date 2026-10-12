package compose

import (
	"encoding/json"
	"errors"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/handline/handline/go/internal/lifecycle"
	"github.com/handline/handline/go/internal/team"
)

// Confinement is what the session of one agent is kept out of, as a terminal session is (handline/vendors/claude.py launch_argv): the
// sandbox's hide lists, and the Read and Edit denies that bind the file tools, which the sandbox does not.
type Confinement struct {
	Hide, NoWrite, ExtraDirs []string
	// ReadAllow are folders the file tools may read without asking (the attachments folder).
	ReadAllow []string
	// SandboxOff is the founder's choice for this agent: commands run unsandboxed. The Read and Edit denies stay.
	SandboxOff bool
}

// SandboxSettings is the sandbox and the permission denies of a session: the shared settings' sandbox (network, writable folders) and
// denies, which the session does not load itself, with the agent's hide lists added to them.
func SandboxSettings(shared []byte, c Confinement) map[string]any {
	var data struct {
		Sandbox     map[string]any `json:"sandbox"`
		Permissions struct {
			Deny []string `json:"deny"`
		} `json:"permissions"`
	}
	_ = json.Unmarshal(shared, &data)
	sandbox := map[string]any{}
	for k, v := range data.Sandbox {
		sandbox[k] = v
	}
	fs := map[string]any{}
	if old, ok := sandbox["filesystem"].(map[string]any); ok {
		for k, v := range old {
			fs[k] = v
		}
	}
	for key, add := range map[string][]string{"denyRead": c.Hide, "denyWrite": c.NoWrite, "allowWrite": c.ExtraDirs} {
		if len(add) == 0 {
			continue
		}
		have, _ := fs[key].([]any)
		for _, p := range add {
			have = append(have, p)
		}
		fs[key] = have
	}
	sandbox["filesystem"] = fs
	sandbox["enabled"] = !c.SandboxOff
	deny := append([]string{}, data.Permissions.Deny...)
	for _, p := range c.Hide { // denyRead binds Bash; the Read tool is a permission
		deny = append(deny, "Read(/"+strings.TrimRight(p, "/")+"/**)")
	}
	for _, p := range c.NoWrite { // likewise denyWrite binds Bash only; an Edit rule covers Write too
		deny = append(deny, "Edit(/"+p+")")
	}
	perms := map[string]any{"deny": deny}
	if len(c.ReadAllow) > 0 {
		var allow []string
		for _, p := range c.ReadAllow {
			allow = append(allow, "Read(/"+strings.TrimRight(p, "/")+"/**)")
		}
		perms["allow"] = allow
	}
	return map[string]any{"sandbox": sandbox, "permissions": perms}
}

// confinementOf is what agent's session in worktree is kept out of.
func (r *Runner) confinementOf(agent, worktree string) (Confinement, error) {
	lc := r.parts.LC
	if lc == nil {
		return Confinement{}, errors.New("no lifecycle to say what the session is kept out of")
	}
	hide, noWrite, err := lc.Launch.Confinement(agent, worktree, claudeTranscripts{})
	if err != nil {
		return Confinement{}, err
	}
	cfg, err := lc.roster.Read(agent)
	if err != nil {
		return Confinement{}, err
	}
	extra, err := lc.Launch.ExtraWorktreesOf(agent, cfg)
	if err != nil {
		return Confinement{}, err
	}
	return Confinement{Hide: hide, NoWrite: noWrite, ExtraDirs: extra, ReadAllow: []string{filepath.Join(lc.Launch.Home, "attachments")}, SandboxOff: !lc.roster.SandboxOn(agent, cfg)}, nil
}

// claudeTranscripts is where the Claude CLI records a session: claude names the folder under the config directory after the worktree, every
// character that is not a letter or a digit becoming a dash.
type claudeTranscripts struct{}

var _ lifecycle.Transcripts = claudeTranscripts{}

func (claudeTranscripts) Name() string { return team.DefaultVendor }

func (claudeTranscripts) TranscriptDir(configDir, worktree string) (string, bool) {
	return filepath.Join(configDir, "projects", nonAlnum.ReplaceAllString(worktree, "-")), true
}

var nonAlnum = regexp.MustCompile(`[^A-Za-z0-9]`)
