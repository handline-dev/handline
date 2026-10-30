-- Step 32 (later): a For you item the founder deferred, until a time or until a task ends. The item is a task, a comment's ask to the founder or a need of the machine, so it is keyed by kind and id, not held on tasks.
CREATE TABLE IF NOT EXISTS later (
    item_kind  TEXT NOT NULL CHECK (item_kind IN ('task', 'comment', 'machine')),
    item_id    TEXT NOT NULL,
    until_at   INTEGER,
    after_task INTEGER,
    set_at     INTEGER NOT NULL,
    PRIMARY KEY (item_kind, item_id),
    CHECK ((until_at IS NULL) <> (after_task IS NULL))
);
