-- table_changes counts the changes of each table a watcher detector reads, bumped by triggers whoever writes. The watcher runs a detector
-- only when a table it names has a new count. A detector that reads a new table needs its three triggers in a later migration
-- (watcher/tables_test.go fails until it has them). events other than eight noisy kinds no detector reads, and cursors other than the
-- watcher's own stream:* ones, are counted in part; every other listed table entirely.
CREATE TABLE IF NOT EXISTS table_changes (name TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID;

INSERT OR IGNORE INTO table_changes(name) VALUES
  ('agent_state'),
  ('agent_view'),
  ('cursors'),
  ('events'),
  ('meta'),
  ('operations'),
  ('prefs'),
  ('prompt_blocks'),
  ('requests'),
  ('session_starts'),
  ('task_blocks'),
  ('task_memos'),
  ('task_requests'),
  ('task_tags'),
  ('tasks');

CREATE TRIGGER IF NOT EXISTS tc_agent_state_i AFTER INSERT ON agent_state
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'agent_state'; END;

CREATE TRIGGER IF NOT EXISTS tc_agent_state_u AFTER UPDATE ON agent_state
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'agent_state'; END;

CREATE TRIGGER IF NOT EXISTS tc_agent_state_d AFTER DELETE ON agent_state
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'agent_state'; END;

CREATE TRIGGER IF NOT EXISTS tc_agent_view_i AFTER INSERT ON agent_view
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'agent_view'; END;

CREATE TRIGGER IF NOT EXISTS tc_agent_view_u AFTER UPDATE ON agent_view
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'agent_view'; END;

CREATE TRIGGER IF NOT EXISTS tc_agent_view_d AFTER DELETE ON agent_view
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'agent_view'; END;

CREATE TRIGGER IF NOT EXISTS tc_cursors_i AFTER INSERT ON cursors
  WHEN NEW.name NOT LIKE 'stream:%'
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'cursors'; END;

CREATE TRIGGER IF NOT EXISTS tc_cursors_u AFTER UPDATE ON cursors
  WHEN NEW.name NOT LIKE 'stream:%'
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'cursors'; END;

CREATE TRIGGER IF NOT EXISTS tc_cursors_d AFTER DELETE ON cursors
  WHEN OLD.name NOT LIKE 'stream:%'
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'cursors'; END;

CREATE TRIGGER IF NOT EXISTS tc_events_i AFTER INSERT ON events
  WHEN NEW.kind NOT IN ('verb_chain', 'rpc_write', 'UserPromptSubmit', 'Stop', 'SubagentStop', 'notified', 'SessionStart', 'SessionEnd')
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'events'; END;

CREATE TRIGGER IF NOT EXISTS tc_events_u AFTER UPDATE ON events
  WHEN NEW.kind NOT IN ('verb_chain', 'rpc_write', 'UserPromptSubmit', 'Stop', 'SubagentStop', 'notified', 'SessionStart', 'SessionEnd')
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'events'; END;

CREATE TRIGGER IF NOT EXISTS tc_events_d AFTER DELETE ON events
  WHEN OLD.kind NOT IN ('verb_chain', 'rpc_write', 'UserPromptSubmit', 'Stop', 'SubagentStop', 'notified', 'SessionStart', 'SessionEnd')
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'events'; END;

CREATE TRIGGER IF NOT EXISTS tc_meta_i AFTER INSERT ON meta
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'meta'; END;

CREATE TRIGGER IF NOT EXISTS tc_meta_u AFTER UPDATE ON meta
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'meta'; END;

CREATE TRIGGER IF NOT EXISTS tc_meta_d AFTER DELETE ON meta
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'meta'; END;

CREATE TRIGGER IF NOT EXISTS tc_operations_i AFTER INSERT ON operations
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'operations'; END;

CREATE TRIGGER IF NOT EXISTS tc_operations_u AFTER UPDATE ON operations
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'operations'; END;

CREATE TRIGGER IF NOT EXISTS tc_operations_d AFTER DELETE ON operations
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'operations'; END;

CREATE TRIGGER IF NOT EXISTS tc_prefs_i AFTER INSERT ON prefs
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'prefs'; END;

CREATE TRIGGER IF NOT EXISTS tc_prefs_u AFTER UPDATE ON prefs
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'prefs'; END;

CREATE TRIGGER IF NOT EXISTS tc_prefs_d AFTER DELETE ON prefs
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'prefs'; END;

CREATE TRIGGER IF NOT EXISTS tc_prompt_blocks_i AFTER INSERT ON prompt_blocks
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'prompt_blocks'; END;

CREATE TRIGGER IF NOT EXISTS tc_prompt_blocks_u AFTER UPDATE ON prompt_blocks
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'prompt_blocks'; END;

CREATE TRIGGER IF NOT EXISTS tc_prompt_blocks_d AFTER DELETE ON prompt_blocks
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'prompt_blocks'; END;

CREATE TRIGGER IF NOT EXISTS tc_requests_i AFTER INSERT ON requests
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'requests'; END;

CREATE TRIGGER IF NOT EXISTS tc_requests_u AFTER UPDATE ON requests
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'requests'; END;

CREATE TRIGGER IF NOT EXISTS tc_requests_d AFTER DELETE ON requests
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'requests'; END;

CREATE TRIGGER IF NOT EXISTS tc_session_starts_i AFTER INSERT ON session_starts
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'session_starts'; END;

CREATE TRIGGER IF NOT EXISTS tc_session_starts_u AFTER UPDATE ON session_starts
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'session_starts'; END;

CREATE TRIGGER IF NOT EXISTS tc_session_starts_d AFTER DELETE ON session_starts
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'session_starts'; END;

CREATE TRIGGER IF NOT EXISTS tc_task_blocks_i AFTER INSERT ON task_blocks
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'task_blocks'; END;

CREATE TRIGGER IF NOT EXISTS tc_task_blocks_u AFTER UPDATE ON task_blocks
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'task_blocks'; END;

CREATE TRIGGER IF NOT EXISTS tc_task_blocks_d AFTER DELETE ON task_blocks
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'task_blocks'; END;

CREATE TRIGGER IF NOT EXISTS tc_task_memos_i AFTER INSERT ON task_memos
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'task_memos'; END;

CREATE TRIGGER IF NOT EXISTS tc_task_memos_u AFTER UPDATE ON task_memos
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'task_memos'; END;

CREATE TRIGGER IF NOT EXISTS tc_task_memos_d AFTER DELETE ON task_memos
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'task_memos'; END;

CREATE TRIGGER IF NOT EXISTS tc_task_requests_i AFTER INSERT ON task_requests
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'task_requests'; END;

CREATE TRIGGER IF NOT EXISTS tc_task_requests_u AFTER UPDATE ON task_requests
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'task_requests'; END;

CREATE TRIGGER IF NOT EXISTS tc_task_requests_d AFTER DELETE ON task_requests
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'task_requests'; END;

CREATE TRIGGER IF NOT EXISTS tc_task_tags_i AFTER INSERT ON task_tags
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'task_tags'; END;

CREATE TRIGGER IF NOT EXISTS tc_task_tags_u AFTER UPDATE ON task_tags
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'task_tags'; END;

CREATE TRIGGER IF NOT EXISTS tc_task_tags_d AFTER DELETE ON task_tags
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'task_tags'; END;

CREATE TRIGGER IF NOT EXISTS tc_tasks_i AFTER INSERT ON tasks
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'tasks'; END;

CREATE TRIGGER IF NOT EXISTS tc_tasks_u AFTER UPDATE ON tasks
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'tasks'; END;

CREATE TRIGGER IF NOT EXISTS tc_tasks_d AFTER DELETE ON tasks
BEGIN UPDATE table_changes SET n = n + 1 WHERE name = 'tasks'; END;
