package lifecycle

import (
	"errors"
	"fmt"
	"os"

	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/states"
	"github.com/handline/handline/go/internal/team"
	"github.com/handline/handline/go/internal/yamlcompat"
)

// Never is a park_timeout_s of 0, global or per agent: the explicit "never close this window" override (a real answer, not an absent key).
const Never = 0

const (
	// BgGraceS: an agent still running background work when its idle window is over is told to finish or stop it, and is stopped this
	// long after that if it shows nothing more.
	BgGraceS = 300
	// BgCheckS is how often an agent with background work is asked which of it to stop.
	BgCheckS = 600
	// NoticeDigestS: results told to an agent that opened work are held this long after the first, then typed once as a digest.
	NoticeDigestS = 900
)

// Settings is how long an idle agent's session may sit before it is closed, the background times and the cache-expiry switch: config.yaml's
// global values, and agent.yaml's own override for the one agent that should behave differently.
type Settings struct {
	Home   string
	Roster *team.Roster
}

func positive(v any) (int64, bool) {
	switch n := v.(type) {
	case int:
		return int64(n), n > 0
	case int64:
		return n, n > 0
	}
	return 0, false
}

func nonNegative(v any) (int64, bool) {
	switch n := v.(type) {
	case int:
		return int64(n), n >= 0
	case int64:
		return n, n >= 0
	}
	return 0, false
}

// config is config.yaml as the readers see it; one that cannot be read is empty.
func (s *Settings) config() map[string]any {
	b, err := os.ReadFile(paths.ConfigFile(s.Home))
	if err != nil {
		return map[string]any{}
	}
	v, err := yamlcompat.Parse(string(b))
	if err != nil {
		return map[string]any{}
	}
	m, _ := v.(map[string]any)
	if m == nil {
		return map[string]any{}
	}
	return m
}

// agentConfig is the agent's own config, {} for one that cannot be read: closing must not stall on a bad agent.yaml.
func (s *Settings) agentConfig(agent string) map[string]any {
	if s.Roster == nil {
		return map[string]any{}
	}
	cfg, err := s.Roster.Read(agent)
	if err != nil || cfg == nil {
		return map[string]any{}
	}
	return cfg
}

// ConfigSeconds is a positive number of seconds from config.yaml, or def when it is absent, not an integer or not above zero.
func (s *Settings) ConfigSeconds(key string, def int64) int64 {
	if n, ok := positive(s.config()[key]); ok {
		return n
	}
	return def
}

// Flag is a true/false setting from config.yaml, or def when it is absent or not one.
func (s *Settings) Flag(key string, def bool) bool {
	if b, ok := s.config()[key].(bool); ok {
		return b
	}
	return def
}

// SetFlag writes one global setting, keeping every other. A config.yaml that cannot be read as a map is not overwritten.
func (s *Settings) SetFlag(key string, value bool) error {
	path := paths.ConfigFile(s.Home)
	var pairs []yamlcompat.Pair
	b, err := os.ReadFile(path)
	switch {
	case errors.Is(err, os.ErrNotExist):
	case err != nil:
		return fmt.Errorf("%s: %w", path, err)
	default:
		var ok bool
		pairs, ok, err = yamlcompat.ParsePairs(string(b))
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		if !ok {
			if len(bytesTrim(b)) != 0 {
				return fmt.Errorf("%s is not a mapping; not overwriting it", path)
			}
			pairs = nil
		}
	}
	set := false
	for i := range pairs {
		if pairs[i].Key == key {
			pairs[i].Val, set = value, true
		}
	}
	if !set {
		pairs = append(pairs, yamlcompat.Pair{Key: key, Val: value})
	}
	text, err := yamlcompat.Dump(pairs)
	if err != nil {
		return err
	}
	return writeAtomic(path, text)
}

func bytesTrim(b []byte) []byte {
	i, j := 0, len(b)
	for i < j && (b[i] == ' ' || b[i] == '\n' || b[i] == '\t' || b[i] == '\r') {
		i++
	}
	for j > i && (b[j-1] == ' ' || b[j-1] == '\n' || b[j-1] == '\t' || b[j-1] == '\r') {
		j--
	}
	return b[i:j]
}

// DefaultCloseTimeoutS is the park timeout every agent without its own override follows.
func (s *Settings) DefaultCloseTimeoutS() int64 {
	if n, ok := nonNegative(s.config()["park_timeout_s"]); ok {
		return n
	}
	return states.IdleCloseS
}

// CloseTimeoutS is the seconds this agent may sit idle before the daemon closes it; Never (0) means it never does. The agent's own
// park_timeout_s wins when it is set, then config.yaml's global one, then the measured default.
func (s *Settings) CloseTimeoutS(agent string) int64 {
	if n, ok := nonNegative(s.agentConfig(agent)["park_timeout_s"]); ok {
		return n
	}
	return s.DefaultCloseTimeoutS()
}

// Never is Never, for a port that cannot name the constant.
func (s *Settings) Never() int64 { return Never }

// SecondsSetting is a positive number of seconds: the agent's own key in agent.yaml wins, then config.yaml's, then def.
func (s *Settings) SecondsSetting(agent, key string, def int64) int64 {
	if n, ok := positive(s.agentConfig(agent)[key]); ok {
		return n
	}
	if n, ok := positive(s.config()[key]); ok {
		return n
	}
	return def
}

// BgNoticeS is how long an agent with background work may show nothing before it is told to finish or stop it: its idle-close window
// unless bg_notice_s says otherwise.
func (s *Settings) BgNoticeS(agent string) int64 {
	return s.SecondsSetting(agent, "bg_notice_s", s.CloseTimeoutS(agent))
}

// BgGraceS is how long after that notice its work is stopped if it still shows nothing.
func (s *Settings) BgGraceS(agent string) int64 {
	return s.SecondsSetting(agent, "bg_grace_s", BgGraceS)
}

// BgCheckS is how often, while it has background work, an agent is asked which of it to stop.
func (s *Settings) BgCheckS(agent string) int64 {
	return s.SecondsSetting(agent, "bg_check_s", BgCheckS)
}

// NoticeDigestS is how long after its first waiting result notice the agent is woken once with a digest.
func (s *Settings) NoticeDigestS(agent string) int64 {
	return s.SecondsSetting(agent, "notice_digest_s", NoticeDigestS)
}

// IgnoreCacheExpiry is whether this agent's resume may reuse its session past the prompt cache's expiry instead of starting fresh: its
// own ignore_cache_expiry wins when set, then config.yaml's global one, off otherwise.
func (s *Settings) IgnoreCacheExpiry(agent string) bool {
	if b, ok := s.agentConfig(agent)["ignore_cache_expiry"].(bool); ok {
		return b
	}
	return s.config()["ignore_cache_expiry"] == true
}
