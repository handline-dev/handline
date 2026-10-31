-- A task answers to the founder through its reporter; it is not handed to them. Each open task owned by human goes back to the agent that
-- handed it over (the last task_handoff to human), else its opener, with a comment '@human <that note>'. Tasks the founder opened (their own, and
-- the daemon's AskFounder) stay.
CREATE TEMP TABLE founder_back AS
SELECT t.id AS task,
       COALESCE((SELECT json_extract(e.payload, '$.from') FROM events e
                  WHERE e.kind = 'task_handoff' AND json_valid(e.payload) AND json_extract(e.payload, '$.task_id') = t.id
                    AND json_extract(e.payload, '$.to') = 'human' AND json_extract(e.payload, '$.from') NOT IN ('human', 'gate')
                  ORDER BY e.id DESC LIMIT 1), t.opened_by) AS back,
       (SELECT json_extract(e.payload, '$.note') FROM events e
         WHERE e.kind = 'task_handoff' AND json_valid(e.payload) AND json_extract(e.payload, '$.task_id') = t.id
           AND json_extract(e.payload, '$.to') = 'human' ORDER BY e.id DESC LIMIT 1) AS note
  FROM tasks t
 WHERE t.owner = 'human' AND t.done_evidence IS NULL AND t.discarded_by IS NULL AND t.opened_by NOT IN ('human', 'gate');
INSERT INTO task_memos(task_id, author, body, created_at)
SELECT task, 'daemon', TRIM('@human ' || COALESCE(note, '')), CAST(strftime('%s', 'now') AS INTEGER) FROM founder_back;
UPDATE requests SET state = 'withdrawn'
 WHERE target = 'human' AND kind = 'task' AND state IN ('queued', 'landed')
   AND id IN (SELECT request_id FROM task_requests WHERE task_id IN (SELECT task FROM founder_back));
UPDATE tasks SET owner = (SELECT back FROM founder_back WHERE task = tasks.id) WHERE id IN (SELECT task FROM founder_back);
DROP TABLE founder_back;
