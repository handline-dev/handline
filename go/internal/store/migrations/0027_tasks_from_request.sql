-- Step 27 (tasks_from_request): the requests read asks, for each request, whether a task came from it; without this each ask scanned every task.
CREATE INDEX IF NOT EXISTS tasks_from_request ON tasks(from_request) WHERE from_request IS NOT NULL;
