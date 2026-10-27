-- Step 28 (memo_tells): which request told which reader of a comment (memo), so the comment can show who has read it.
CREATE TABLE IF NOT EXISTS memo_tells (
  memo_id INTEGER NOT NULL,
  reader TEXT NOT NULL,
  request_id INTEGER NOT NULL,
  PRIMARY KEY (memo_id, reader)
);
