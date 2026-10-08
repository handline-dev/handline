package structured

import (
	"database/sql"
	"encoding/json"
	"time"
)

// DBPrompts keeps the open permission prompt of an agent in prompt_blocks, the row prompt.list reads.
type DBPrompts struct {
	DB  *sql.DB
	Now func() time.Time
}

func (s *DBPrompts) now() int64 {
	if s.Now != nil {
		return s.Now().Unix()
	}
	return time.Now().Unix()
}

func (s *DBPrompts) Open(agent string, p PromptRow) (int64, error) {
	now := s.now()
	// A row left open by a daemon that died would hold the one-open-prompt-per-agent index.
	if _, err := s.DB.Exec("UPDATE prompt_blocks SET gone_at = ? WHERE agent = ? AND gone_at IS NULL", now, agent); err != nil {
		return 0, err
	}
	panel, _ := json.Marshal(p.Lines)
	p.Detail["title"], p.Detail["lines"], p.Detail["since"], p.Detail["wait"] = p.Title, p.Lines, now, nil
	detail, _ := json.Marshal(p.Detail)
	res, err := s.DB.Exec("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, wait_id, answerable, first_seen, last_seen) "+
		"VALUES(?, 'permission', ?, ?, ?, ?, NULL, 1, ?, ?)", agent, p.Fingerprint, p.Title, string(panel), string(detail), now, now)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

func (s *DBPrompts) Touch(id int64) error {
	_, err := s.DB.Exec("UPDATE prompt_blocks SET last_seen = ? WHERE id = ? AND gone_at IS NULL", s.now(), id)
	return err
}

func (s *DBPrompts) Close(id int64, answered, ok bool, said string) error {
	now := s.now()
	if !answered {
		_, err := s.DB.Exec("UPDATE prompt_blocks SET gone_at = ? WHERE id = ?", now, id)
		return err
	}
	okInt := 0
	if ok {
		okInt = 1
	}
	_, err := s.DB.Exec("UPDATE prompt_blocks SET gone_at = ?, answered_at = ?, answer_ok = ?, answer_said = ? WHERE id = ?", now, now, okInt, said, id)
	return err
}
