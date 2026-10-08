# Tellings the Go task writes leave out

The tellings are ported: what Python does after the commit of a task write to tell an agent (a typed say, a delivery, a push) is the
supervisor's telling layer (go/internal/supervisor), which the write methods call through taskwrites.Teller. They are not yet called:
no daemon builds a supervisor Env until the owners of its ports (observe, blocked, lifecycle, evaluate, the terminal channel) have
landed, so daemon.Config.Teller is nil and every write below still tells nothing. The cutover waits until the table is empty.

| Method | Python | What Go leaves out |
|---|---|---|
| task.memo.add, task.memo.edit, task.memo.delete | actions.memo_*, tell_memo_holder | the typed say to the holder of a doing task, until a daemon passes a Teller |
| task.discard, task.withdraw | actions.discard_task, after the commit | the follow-up after the commit (says typed, the owner's next task, freed owners, inbox files), until a daemon passes a Teller |
| task.queue, task.draft | actions.set_queued_all, _queue_deliver | the delivery after the commit and the sentence saying whether the task went out, until a daemon passes a Teller |
