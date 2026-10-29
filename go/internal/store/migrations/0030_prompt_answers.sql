-- Step 30 (prompt_answers): what prompt.answer answered, by prompt (prompt_blocks.id), so a closed prompt in the chat says what it got.
CREATE TABLE IF NOT EXISTS prompt_answers (
  prompt_id INTEGER PRIMARY KEY,
  agent     TEXT    NOT NULL,
  kind      TEXT    NOT NULL,
  choice    TEXT    NOT NULL,
  label     TEXT    NOT NULL,
  note      TEXT,
  by_name   TEXT    NOT NULL,
  at        INTEGER NOT NULL
);
