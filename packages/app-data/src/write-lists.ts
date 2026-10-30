// Lists of write methods kept beside the writes table, each for its own reason.

/** What a structured agent has no terminal for: a write of these to one is a bug in the view, not a refusal to wait for. */
export const NOT_FOR_STRUCTURED = ['session.input', 'session.keys', 'session.type', 'input.take', 'input.release'] as const;

/** The pairing writes run outside the cache: the redeem before hello, from the pair flow; the push token once paired, from the OS. */
export const PAIRING = ['device.redeem', 'device.push_token'] as const;

/** Agent commands: an agent sends these, the UI never does. Guard test 2 reads this list. */
export const NOT_FROM_UI = [
  'session.wake', // a message to a closed agent starts it; no screen wakes one by hand
  'say',
  'hook.record',
  'task.wait', // hl task wait; no screen opens a gate yet
  'hr.propose',
  'trust.propose',
  'task.begin',
  'task.start',
  'task.memo.add', // the old names of task.comment.*; the screens call the new ones
  'task.memo.edit',
  'task.memo.delete',
  'task.progress',
  'task.handoff',
  'worktree.declare', // hl worktree add: an agent records the worktree it works in
  // `hl withdraw` and `hl cancel`: the app takes a message back through chat.cancel.
  'request.withdraw',
  'request.cancel',
] as const;
