// Generated from contract/protocol.json by contract/generate.py; do not edit.
/* eslint-disable */

/** UTC, RFC 3339. */
export type Timestamp = string;

/** An agent's name, as hl team lists it. */
export type AgentName = string;

/** Ordered lowest to highest. A method's role is the lowest that may call it; every higher role may too. */
export type Role = "anonymous" | "agent" | "member" | "founder";

/** chat is added for the chat view (the UI design update): messages, landing state, transcript items and working state of one agent; events carry `agent`. devices (founder only, subscribed on its own, live only): pairing and revocation of devices. hr: HR proposals, so a client's tab dot is live; any proven caller, as hr.list. transcript: the per-agent event stream of a structured session (transcript.event), served only for an agent the daemon runs as a structured session; see the Transcript stream section. */
export type Topic = "status" | "accounts" | "agents" | "inbox" | "tasks" | "notifications" | "operations" | "chat" | "devices" | "hr" | "transcript";

export type Caller = {
  kind: "person" | "device" | "agent" | "anonymous";
  /** The user, the device's name, or the agent's name. */
  name: string;
  /** Device id, when kind is device. */
  device?: string;
};

/** Who did a write; recorded on its event. */
export type Actor = {
  kind: "user" | "device" | "agent" | "daemon";
  name: string;
  device?: string;
};

/** Every write's result. A refusal is the `refused` error, never an accepted:false result. */
export type Accepted = {
  accepted: true;
  /** One sentence for a person, the legacy `said`. */
  said: string;
  /** True when this is the stored reply to a retried request_id; the action was not repeated. */
  replayed?: boolean;
  [key: string]: unknown;
};

export type RefusalData = {
  code: "refused";
  /** The gate's own sentence. */
  reason: string;
  /** Stable id of the gate that refused; see x-gates. */
  gate?: string;
  /** The command or step that fixes it, when there is one. */
  hint?: string;
  /** With gates proposal_changed and proposal_settled: the proposal as it is now. */
  proposal?: Proposal;
  /** With gate confirm: what the call would do and who is told. */
  plan?: string;
};

/** Like 2h, 30m, 1d. */
export type Duration = string;

export type InputHolder = {
  kind: "daemon" | "person" | "device";
  /** `daemon`, or the person or device. */
  name: string;
  since: Timestamp | null;
};

export type PromptOption = {
  /** What prompt.answer takes as `choice`. */
  key: string;
  label: string;
  answer?: "yes" | "no" | "other";
  highlighted?: boolean;
};

export type Prompt = {
  /** Opaque; changes whenever the open prompt changes (the daemon uses the screen block's fingerprint). */
  id: string;
  agent: AgentName;
  /** permission, question, dialog or exit_confirm today; open set. A dialog is `dialog` unless its screen is a known one (contract/prompts/). */
  kind: string;
  tool?: string | null;
  command?: string | null;
  reason?: string | null;
  /** The dialog's panel as it is on screen, one string per line. */
  lines: string[];
  /** The choices, parsed from the dialog. Empty until the session-control task fills it; a client falls back to lines. */
  options: PromptOption[];
  /** When the daemon first saw this prompt open. */
  since: Timestamp;
  /** Several options may be picked, then submitted: prompt.answer takes `choice` as an array of PromptOption.key. Absent means false, as for every other prompt, where an array is bad_request. */
  multi?: boolean;
};

export type Block = {
  kind: string;
  since: Timestamp;
};

/** The stored per-agent view; the daemon decided every field. */
export type AgentView = {
  name: AgentName;
  /** The first line of the agent's ROLE.md that is not blank and not a heading, as one line, at most 160 characters; empty when there is none. */
  role: string;
  model: string | null;
  effort: string | null;
  /** One of x-state-words, lower case; an open set, clients treat an unknown word as idle. */
  state: string;
  state_since: Timestamp;
  block?: Block | null;
  wall_reason?: string | null;
  background_since?: Timestamp | null;
  would_wake?: boolean;
  blocker?: string | null;
  account: string | null;
  task: {
    id: number;
    title: string;
  } | null;
  cache_until: Timestamp | null;
  /** Whole percent of the context window the agent's own session has used; null when it has no account, no session of its own, or a usage snapshot too old to trust. A view without the field is the same as null. */
  ctx_pct?: number | null;
  has_dialog: boolean;
  parent: AgentName | null;
  /** Who holds the input; one in v1, a list so more can later. A structured agent has no input to hold: []. */
  input_holders?: InputHolder[];
  /** The text drawn beside the state word (a clock, what a wait is for), at most 40 characters; empty when there is none. The state column is drawn from state and state_detail alone. */
  state_detail: string;
  /** Only with agents.list `status: true` (what `hl team` draws): whether the agent has ever run (a state row exists). */
  started?: boolean;
  /** Only with agents.list `status: true` (what `hl team` draws): whether its config names a repo. */
  has_repo?: boolean;
  /** Only with agents.list `status: true` (what `hl team` draws): whether a terminal is recorded for it. */
  has_terminal?: boolean;
  /** Only with agents.list `status: true` (what `hl team` draws): the word the screens draw for its state (RUNNING, IDLE, PENDING, MOVING, DIALOG, FATAL), `tui.screen_state`'s and not the wire `state`; null when it never started. */
  screen_word?: string | null;
  /** Only with agents.list `status: true` (what `hl team` draws): the text drawn beside screen_word. */
  screen_detail?: string;
  /** Only with agents.list `status: true` (what `hl team` draws): requests, messages and answers owed to it that have not reached it. */
  unreached?: number;
  /** Only with agents.list `status: true` (what `hl team` draws): its tasks that wait on a hold, oldest first. */
  task_waits?: {
    task: number;
    /** The daemon's reason the task waits. */
    words: string;
  }[];
  /** Only with agents.list `status: true` (what `hl team` draws): what holds back a wake that is due (set only while would_wake is true). */
  wake_wait_reason?: string | null;
  /** The agent is set up to run as a structured session, whether its session is live or closed: the transcript topic and transcript.events serve it (the stored log too), chat.send returns a receipt for it, and it has no terminal to watch. Absent means false (a terminal agent), so a client needs no transcript.events probe to choose its view. Sent in agent.changed too. */
  structured?: boolean;
  /** What the agent is, for a column (agents.kind): the model and its effort as `<model> <effort>`, effort one of low, mid, high, xhi, max (any other effort is shown whole), with the vendor in front as `<vendor> <model> <effort>` only when it is not claude. Absent from a daemon that does not serve it yet. */
  kind?: string;
  /** The tasks the agent owns that are not done: the current one and those waiting (queued or doing). A draft that is not the current task is not counted, as it is not approved yet; one the agent picked up and made current is. Absent from a daemon that does not serve it yet. */
  task_count?: number;
  /** The agent is idle (the screen word IDLE), has a current task, that task is queued, and no open blocker holds it: it can be given work now. Absent means false. */
  ready?: boolean;
  /** What holds back a wake that is due (the screens' wall cell): the account that would wake it. Null when would_wake is false or nothing holds it back. Absent from a daemon that does not serve it yet. */
  wall?: {
    /** The account whose window the wake waits on. */
    account: string;
    /** When that window rolls over, as unix seconds: the shortest walled window, the longest when only that stands. Null when the reset is not known or the wait is for a slot (the account is at its cap), not a window. */
    clock: number | null;
    /** The account is a guess (nothing has confirmed the one the resume is tied to), not the account itself. */
    assumed: boolean;
  } | null;
  /** A trigger about to fire (the home row's warning cell): the daemon would hand this agent off at its next tick. `usage` is the shortest window at the wall, `usage_7d` the longest, `context` the context at its limit. Null when none, and for an agent whose triggers do not apply (closed, no terminal, parked, throttled or degraded). Needs a live evaluate over the agent's current usage and context readings, not a stored value. Absent from a daemon that does not serve it yet. */
  fire?: ("usage" | "usage_7d" | "context") | null;
  /** What this agent alone asks of the founder, the most urgent of: `ask` (an open ask to the founder), `alert` (the screen word needs a human, or a delivery block the founder has been told of), `stuck` (a delivery that is not moving, or a usage limit), `running` (a turn is going on), in that order. Null when none. The daemon says nothing of folds, which are the screen's: a screen that hides some of an agent's reports (found through `parent`, all the way down) ranks `ask` first, then `alert` and `stuck` together, then `running`. It draws the mark of the first rank any of them has and counts those in that rank; `alert` and `stuck` share one mark and one count, red if any of them is `alert`, yellow otherwise (so one `alert` and one `stuck` make a red mark counting 2). Read from stored state, no live evaluate. Absent from a daemon that does not serve it yet. */
  attention?: ("ask" | "alert" | "stuck" | "running") | null;
  /** The agent's resume reuses its session whatever the prompt cache did (its own ignore_cache_expiry, else the global one), so a countdown from cache_until means nothing. Read from configuration. Absent means false. */
  cache_ignored?: boolean;
  /** Why the agent's configuration cannot be read, as one line; null when it can. When set, `kind` is absent (and the model, effort and the other fields that come from the configuration are null or absent). Absent from a daemon that does not serve it yet. */
  config_error?: string | null;
  /** Set while the agent waits on a decision only a person at its terminal can make. Fed by hooks (PermissionRequest and the permission and question Notification kinds) and by the session file saying status `waiting` with `waitingFor` (`permission prompt` is the one measured; the file has no needs or tempo key), never by reading the screen; the tool and its target come from the PermissionRequest hook event; cleared by the hook that follows (PostToolUse, UserPromptSubmit, Stop, SessionEnd) or when the session file stops saying it. While set, `state` is `waiting`, `has_dialog` is true and `attention` is `alert`. null when none. Absent from a daemon that does not serve it yet (the Python daemon reads the screen and sets has_dialog and `alert` without it). */
  needs_you?: NeedsYou | null;
};

export type AgentDetail = AgentView & {
  worktree: {
    path: string;
    branch: string;
  } | null;
  terminal: string | null;
  session_id: string | null;
  /** A session is running. For a structured agent, its session is live (terminal is null). */
  session_open: boolean;
  /** The account the daemon would place a session start on, before session.start; account null with a reason when none can take work. */
  account_pick: {
    account: string | null;
    reason: string;
  };
  /** The agent's open prompt, or null. */
  prompt: Prompt | null;
  settings?: AgentSettingValues;
  /** The directory a shell command for this agent runs in: its worktree when that is on disk, else the daemon's home directory. worktree.path is what the agent's record holds and may name a directory that is gone; cwd is always usable. */
  cwd: string;
  /** Whether each action of the chat page's menu is worth offering now, by the daemon's own rules: ok, and why not when it is not. */
  can: {
    terminal: {
      ok: boolean;
      why: string | null;
    };
    wake: {
      ok: boolean;
      why: string | null;
    };
    close: {
      ok: boolean;
      why: string | null;
    };
    restart: {
      ok: boolean;
      why: string | null;
    };
    park: {
      ok: boolean;
      why: string | null;
    };
    fold: {
      ok: boolean;
      why: string | null;
    };
    move: {
      ok: boolean;
      why: string | null;
    };
  };
};

export type TimelineEntry = {
  id: number;
  at: Timestamp;
  /** The recorded event kind; open set. state, task_delivered, handoff, key, input, interrupt, start and stop are expected once those are recorded. */
  kind: string;
  actor: Actor | null;
  /** One line to show; falls back to the kind. */
  text: string;
  /** The event's whole payload. */
  data?: { [key: string]: unknown };
};

/** The task's clock. */
export type TaskClock = {
  /** The time box in seconds. */
  length_s: number;
  /** True when the length is the daemon's default, none was set. */
  default: boolean;
  start: Timestamp | null;
  end: Timestamp | null;
  overdue_since: Timestamp | null;
  /** How many overrun notices the opener has had. */
  told: number;
};

/** A task row without its body. */
export type Task = {
  id: number;
  from: string;
  to: string;
  title: string;
  state: string;
  /** e.g. manager. */
  relation: string;
  /** e.g. async. */
  response: string;
  time_box: Duration | null;
  parent: number | null;
  created: Timestamp;
  delivered: Timestamp | null;
  done: Timestamp | null;
  hold_reason: string | null;
  block_reason: string | null;
  evidence: string | null;
  /** 1 high, 2 default, 3 low (written P1-P3). */
  priority?: 1 | 2 | 3;
  /** The newest of the task's creation, edit, done and discard and of any request linked to it: what a list sorts a person's tasks by. Absent from a daemon that does not send it. */
  last_activity?: Timestamp;
  /** Who last changed the task's owner, title, priority or brief; null when never. */
  edited_by?: string | null;
  /** Who the task answers to when it closes: its reporter when one was set; else the founder (`human`) if any request linked to it came from the founder, else whoever opened it. */
  asker?: string;
  /** Null when the task was not discarded. */
  discarded?: {
    by: string;
    reason: string;
    at: Timestamp;
  } | null;
  /** Where the task's state and its delivery disagree (a draft with work out, an approved task nothing is out for), in words; null when they agree. */
  mismatch?: string | null;
  /** The requests linked to the task, oldest first. */
  requests?: {
    id: number;
    from: string;
    to: string;
    state: string;
    at: Timestamp;
  }[];
  /** The ids of the open tasks that hold this one (block_reason names them as text); empty when nothing does. Absent from a daemon that does not send it. */
  blocked_by?: number[];
  /** The task's clock, the same object task.get gives; null once it is done or discarded. Absent from a daemon that does not send it. */
  time?: TaskClock | null;
};

export type TaskDetail = Task & {
  body: string;
  deliveries: {
    at: Timestamp;
    how: string;
    outcome: string;
  }[];
  events: TaskEvent[];
  memos: Memo[];
  blockers: {
    task: number;
    reason: string;
    owner: string;
    title: string;
  }[];
  children: number[];
  handoffs: {
    at: Timestamp;
    from: string;
    to: string;
    note: string;
  }[];
  /** Every change of the assignee or the owner made by an edit, oldest first; anyone may make one. Handoffs are listed in handoffs. */
  assignments?: {
    at: Timestamp;
    /** Who made the change. */
    by: string;
    /** assignee: who works the task (its owner column); owner: who it answers to (its reporter). */
    what: "assignee" | "owner";
    from: string;
    to: string;
  }[];
  /** Who the task answers to when it closes: its reporter when one was set; else the founder (`human`) if any request linked to it came from the founder, else whoever opened it. A client lets the founder reopen a done task only when this is the founder. */
  asker: string;
  discarded: {
    by: string;
    reason: string;
    at: Timestamp;
  } | null;
  merged_into: number | null;
  /** The task's clock; null once it is done or discarded. */
  time: TaskClock | null;
  parent_title: string | null;
  /** The sub-tasks by number: `children` with their titles, owners and states. */
  subtasks: {
    id: number;
    title: string;
    owner: string;
    state: string;
  }[];
  /** What happened on the task as one list, oldest first: its memos, its handoffs (a handoff's note is its entry, not a second one) and the requests linked to it. Overlaps `events`, `memos`, `handoffs` and `deliveries`, which stay. */
  timeline: {
    at: Timestamp;
    /** The author, or `from → to` for a handoff. */
    who: string;
    /** memo; handoff; a request linked to the task; edit, the brief changed (text is the new brief); state, a change of state (text is `from -> done`). */
    kind: "memo" | "handoff" | "request" | "edit" | "state";
    /** Already stripped of control characters: a client draws it as it is. */
    text: string;
    memo: number | null;
    edited_at: Timestamp | null;
  }[];
  /** Whom the founder can ask about the task (task.ask), each agent once. */
  people: {
    /** Who the founder last chatted with, latest first. */
    recent: string[];
    /** The rest of the holder's team, in team.yaml order. */
    team: string[];
    others: string[];
  };
  /** The task was opened as a ui task. Absent when it was not. */
  ui?: boolean;
  /** What the task's end kept beyond its evidence. Absent for a task without any. */
  result?: {
    /** The full sha it was done at. */
    commit?: string;
    /** Whether main reaches commit. */
    on_main?: boolean;
    /** Fetch each with task.shot. */
    shots: {
      n: number;
      bytes: number;
    }[];
  };
};

export type DecisionOption = {
  id: string;
  label: string;
};

export type InboxItem = {
  id: number;
  kind: string;
  agent: AgentName | null;
  title: string;
  body: string;
  evidence: string | null;
  task: number | null;
  created: Timestamp;
  read: boolean;
  answered: boolean;
  options: DecisionOption[];
  prompt: Prompt | null;
  /** Only with inbox.list `queue: true`, for an agent's own item: the line `hl inbox` ends it with, what the agent does to close it (delivery.answer_verb for a task). */
  how_to_close?: string;
};

export type Event = {
  /** Opaque, increasing integer: the daemon's own stream position, not the id of any other table's row. A client keeps the newest one it saw and resumes from it. */
  cursor: number;
  topic: Topic;
  /** See x-topics for each topic's types. */
  type: string;
  at: Timestamp;
  actor: Actor | null;
  agent: AgentName | null;
  title: string | null;
  target: {
    screen: string;
    /** Agent name, task or item id. */
    id: string;
  } | null;
  data: { [key: string]: unknown };
};

/** An event as task.get lists it: the fields of Event but no cursor, because task events are not on the stream and have no position in it. */
export type TaskEvent = {
  topic: Topic;
  /** See x-topics for each topic's types. */
  type: string;
  at: Timestamp;
  actor: Actor | null;
  agent: AgentName | null;
  title: string | null;
  target: {
    screen: string;
    /** Agent name, task or item id. */
    id: string;
  } | null;
  data: { [key: string]: unknown };
};

export type Window = {
  used_pct: number;
  resets_at: Timestamp | null;
  /** Where the number comes from, e.g. measured or estimated. */
  source: string;
};

export type Account = {
  name: string;
  login_state: "ok" | "needs_login" | "unknown";
  five_hour: Window | null;
  weekly: Window | null;
  agents: AgentName[];
  moves: {
    at: Timestamp;
    agent: AgentName;
    from: string | null;
    to: string;
    reason: string;
  }[];
  /** The name screens show; the account's own name until an alias is set. */
  label: string;
  /** The account's own cap: most agents mid-turn at once; null follows active_cap. */
  cap: number | null;
  /** The cap in force: cap, else the active_cap setting. */
  cap_effective: number;
  /** Whether a dead usage read may spend one real turn to fix itself (hl account recover). */
  recover: boolean;
  cycle: {
    /** One-month subscription; off is renewing. */
    on: boolean;
    end: string | null;
    /** end is the next monthly anniversary of the subscription start, not typed. */
    end_is_default: boolean;
  };
  /** Only with accounts.list `roster: true`. Agents on this account, running or not (the figure `hl account list` prints). */
  agents_assigned?: number;
  /** Only with accounts.list `roster: true`. Those of them that are mid-turn or otherwise running. */
  agents_running?: number;
  /** Only with accounts.list `roster: true`. Agents whose config cannot be read and so count on no account; the same on every account, and every figure above is a floor while it is above 0. */
  agents_unplaced?: number;
  /** The login in progress or the last one to end; null when none was started. Reported on the accounts topic as account.changed. Never carries the authorize URL: only the founder's account.login reply has it. */
  login?: {
    login_id: string;
    state: "waiting" | "code_sent" | "done" | "failed" | "cancelled";
    /** Why the login ended failed, one plain line (a wrong code, or the account is logged in as the same Anthropic account as <names>); absent while it has not failed. */
    reason?: string;
  } | null;
  /** Only with accounts.list `usage: true`. The plan claude reports (`max`, `pro`), null when not recorded. */
  plan?: string | null;
  /** Only with accounts.list `usage: true`. The shortest and, when the vendor has one, the longest window: the figure read, the one that may be acted on, and where it came from. */
  windows?: {
    window: string;
    minutes: number;
    /** The figure as read. */
    pct: number | null;
    /** The figure the daemon acts on. */
    effective: number | null;
    /** Where the figure comes from, or why it is not trusted. */
    source: string;
    /** Unix seconds the window resets. */
    reset: number | null;
  }[];
  /** Only with accounts.list `usage: true`. Context used by the account's last session, percent. */
  ctx_pct?: number | null;
  /** Only with accounts.list `usage: true`. Seconds since the usage was read. */
  age_s?: number | null;
  /** Only with accounts.list `usage: true`. How long the account lasts at its recent burn, in words (`~10m to wall`), as `hl account list` prints it. */
  runway?: string;
  /** Only with accounts.list `usage: true`. Whether one more agent may start on it. */
  assignable?: boolean;
  /** Only with accounts.list `usage: true`. Why assignable is what it is. */
  assign_reason?: string;
};

export type DaemonStatus = {
  alive: boolean;
  /** Loaded git sha. */
  version: string;
  protocol: number;
  schema: number;
  started_at: Timestamp;
  terminal_host: {
    name: string;
    health: "ok" | "degraded" | "down";
    detail?: string;
  };
  agents: {
    total: number;
    by_state: { [key: string]: number };
  };
  inbox: {
    unread: number;
  };
};

export type TeamAgent = {
  name: AgentName;
  role: string;
  parent: AgentName | null;
  role_file: {
    path: string;
    updated: Timestamp | null;
  };
  base_file: {
    path: string;
    updated: Timestamp | null;
  };
};

export type Proposal = {
  id: number;
  /** The agent the proposal is about, as the card heads it `<kind> <agent>`. For a bundle: `<N> changes`, not an agent's name. */
  agent: string;
  /** One line, the card's head: `<kind> <agent>`, with ` -> <to>` after it for link and unlink; a bundle's is `bundle <N> changes`. Text from agents' names; draw as text. */
  title: string;
  /** What approving does, in words, one change per line. Not a unified diff. */
  diff: string;
  /** open: waits for the founder. approved: decided and applied. rejected. apply_failed: approved by the founder but not applied (the apply failed, or never ran): still the founder's move, and hr.decide approve again applies it. withdrawn: taken back by its author or the founder. An open set: a client draws a word it does not know as text, offers no action on it and does not count it. See hr.list for the Python store's own states. */
  state: string;
  created: Timestamp;
  /** Digest of what the proposal changes as drawn; hr.decide's approve hands it back. */
  subject: string;
  /** Who asked: an agent's name. Never human: hr.list leaves out what the founder did directly. Text from the daemon's record; optional. */
  requested_by?: string;
  /** Why it asks, as the agent wrote it. Text only, from an agent: a client draws it as plain text, never as markup, and drops control characters. Optional. */
  reason?: string;
  /** The fields the proposal asks for (vendor, model, effort, team, repo, to, then each `set <key>`). Every value is text from an agent (same rule as `reason`); optional, and a key it did not ask for is absent. Absent for kind bundle: its `diff` carries the changes. members, repos, relation, response and context texts are in `diff` only. */
  asked?: {
    vendor?: string;
    model?: string;
    team?: string;
    repo?: string;
    /** rename: the new name; link, unlink: the other end. */
    to?: string;
    /** `set <key>`: each agent setting asked for, key to value. */
    sets?: { [key: string]: string };
    effort?: string;
  };
  error?: string | null;
  settled?: Timestamp | null;
  /** What the proposal does: add, retire, rename, link, unlink, team, settings, context or bundle (several changes decided once). An open set: a client draws a word it does not know as text. Optional until the Python hr.list sends it; a client then reads the first word of `title`. */
  kind?: string;
};

export type Repo = {
  name: string;
  path: string;
  default_branch: string;
};

export type ChangedFile = {
  path: string;
  /** M, A, D, R or ?. */
  status: string;
  added: number;
  deleted: number;
};

export type Worktree = {
  path: string;
  repo: string;
  branch: string;
  owner: AgentName | null;
  parent: string | null;
  children: string[];
  status: string;
  changed: ChangedFile[];
};

export type AgentOptions = {
  repos: Repo[];
  roles: {
    name: string;
    description: string;
  }[];
  models: string[];
  efforts: string[];
  accounts: {
    name: string;
    login_state: string;
  }[];
  free_names: string[];
};

export type Operation = {
  id: string;
  kind: string;
  state: "running" | "done" | "failed";
  step: string | null;
  steps_done: number;
  steps_total: number | null;
  message: string | null;
  result: { [key: string]: unknown } | null;
  agent: AgentName | null;
};

/** The result of a write that starts a long-running operation. */
export type OperationStarted = Accepted & {
  /** The id of the operation; operation.get and the operations topic follow it. */
  operation: string;
};

export type Schedule = {
  id: number;
  target: AgentName;
  cron: string;
  message: string;
  next_run: Timestamp | null;
  last_run: Timestamp | null;
  set_by: string;
};

export type Stats = {
  window: "24h" | "7d";
  since: Timestamp;
  until: Timestamp;
  tasks_done: number;
  working_seconds: { [key: string]: number };
  moves: number;
  handoffs: number;
  blocks: number;
  per_account: {
    account: string;
    five_hour_used_pct: number | null;
    weekly_used_pct: number | null;
  }[];
};

export type Settings = {
  timings: { [key: string]: number };
  terminal_host: {
    choice: string;
    available: string[];
    health: "ok" | "degraded" | "down";
  };
  listener: Listener;
  trust_folders: {
    account: string;
    agent: AgentName | null;
    path: string;
  }[];
  hooks: {
    installed: boolean;
    accounts: {
      account: string;
      installed: boolean;
    }[];
  };
  values: SettingsValues;
  /** With settings.get `agent`: only that agent's own values for the Per agent keys. */
  overrides?: { [key: string]: unknown };
};

export type Device = {
  id: string;
  name: string;
  role: "member";
  created: Timestamp;
  last_seen: Timestamp | null;
  connected: boolean;
};

/** One terminal row: plain text, no escapes; in format `styled` also its style runs. */
export type Row = {
  text: string;
  /** Set only in format `styled`, never in `text` (those rows have none). Sorted by `from`, not overlapping, none empty, only runs whose style is not the default; what no span covers is drawn in the default style. `text` is the same string `text` would send, so a client that ignores spans loses nothing. */
  spans?: {
    /** First character, counted in Unicode code points of `text`, 0-based. */
    from: number;
    /** One past the last, so the run is [from, to). May go past the end of `text` for blank cells with a background; see x-terminal-styled `spans` for how those count. */
    to: number;
    style: SpanStyle;
  }[];
};

/** Text and styled attaches; null when hidden or above the frame. `x` is in code points of the row's `text`, like `Row.spans`; `y` is in the frame's rows and may be len(lines) or more. Rules: `x-terminal-styled`, Cursor. */
export type Cursor = {
  x: number;
  y: number;
} | null;

export type TerminalSnapshot = {
  terminal: string;
  /** `styled` is text rows with `spans`; see x-terminal-styled. */
  format: "text" | "styled";
  seq: number;
  cols: number;
  rows: number;
  lines: Row[];
  cursor: Cursor;
  history: {
    /** Absolute index of the first line returned. */
    first: number;
    lines: Row[];
    more: boolean;
  };
  /** The first of holders; kept for clients written to the single value. */
  holder: InputHolder;
  /** Everyone who holds the input, oldest first; one in v1. */
  holders: InputHolder[];
};

export type Memo = {
  id: number;
  task: number;
  author: string;
  at: Timestamp;
  text: string;
  /** When the memo's text was last changed; null if never. */
  edited_at?: Timestamp | null;
};

export type TaskBoardRow = {
  task: Task;
  holder: AgentName | null;
  /** Who has the task now, read from its linked requests: `waiting` on open blocker tasks (`tasks`); `held` by the agents a request is with (`agents`); `sub_task` when an open child task's request is with them (`agents`); `held_as_sub_task` while an open child shares a request with it (`tasks`, `agents` their owners); `result_in` when the last request came back done from someone other than the caller or the founder; else `nobody`. */
  now?: {
    kind: "waiting" | "held" | "sub_task" | "held_as_sub_task" | "result_in" | "nobody";
    agents: string[];
    tasks: number[];
  };
};

/** Landing state of a message; open set. */
export type Landing = "queued" | "typed" | "delivered" | "delivered_unanswered" | "answered" | "dismissed" | "withdrawn";

/** One request between the person and an agent. */
export type ChatMessage = {
  /** Request id. */
  id: number;
  at: Timestamp;
  /** When the request entered its current state (queued, typed, landed, withdrawn...). Never null; the creation time until it first changes. */
  state_at: Timestamp;
  from: string;
  to: string;
  state: string;
  landing: Landing;
  sending_reason: string | null;
  body: string;
  answer: {
    id: number;
    text: string;
    at: Timestamp;
  } | null;
  dismissed: boolean;
  delivered_at: Timestamp | null;
  typed_at: Timestamp | null;
  landed_at: Timestamp | null;
  parent: number | null;
  kind: string;
  tasks: {
    id: number;
    title: string;
  }[];
  is_task_question: boolean;
  /** Only in chat.search hits: the agent whose chat shows the message. */
  agent?: string;
  /** Only in chat.search hits: which of that agent's chats shows it (the jump is chat.locate). */
  scope?: "mine" | "all";
  /** Only for a message to a structured agent, while it is drawn (a withdrawn one is not). Changes ride the chat event. See x-delivery. */
  receipt?: Receipt;
  /** The images sent with the message, in order. Absent when it has none. */
  attachments?: Attachment[];
  parent_item?: {
    /** The transcript item id. chat.transcript with since=id reads the page that shows it. */
    id: number;
    /** The reply's first 300 characters (Unicode code points, no ellipsis) when the message was sent. */
    text: string;
  } | null;
};

export type TranscriptItem = {
  /** Its time and its source's row, so it never changes. An item that arrives in a second a client has read up to can sort before the last one it has: `since` reads from that second and a client drops the ids it already holds. For kind reply it is also what chat.send takes as parent_item. A reply has one of two sources, told apart by the last digit of its number within the second: a structured session's text block (even; the same id on every read and in the live event's `item`) and the answer to a request (odd); each is the same on every read, and chat.transcript lists both. */
  id: number;
  at: Timestamp;
  kind: "reply" | "tool" | "prompt" | "task_event" | "session";
  text?: string;
  tool?: string;
  /** For kind tool: the text the tool printed, as the store holds it. Absent when the store holds none. */
  output?: string;
  /** For kind tool: how many lines the store cut from the end of the output (it keeps the start). 0 or absent when nothing was cut. */
  dropped_lines?: number;
  /** Prompt card; `open` says whether it can still be answered. */
  prompt?: Prompt;
  open?: boolean;
  task_event?: {
    event: "opened" | "changed" | "done" | "withdrawn";
    task: number;
    request: number | null;
    evidence: string | null;
    /** What a `changed` event changed, as chat.requests words it: `assigned to X, priority 2 -> 1`, `title changed`, `handed to X`. Absent when the event word says it all. An open set: a client draws a word it does not know as text. */
    change?: string;
  };
  /** For kind session: a session of this agent began here. */
  session?: {
    id: string;
    started: Timestamp;
    handoff: string | null;
  };
};

export type Working = {
  turn_running: boolean;
  line: string | null;
  since: Timestamp | null;
};

export type Capabilities = {
  chat: boolean;
  /** Worktree review beyond worktree.diff; false in v1, the UI hides the Review tab. */
  review: boolean;
  /** Formats the daemon serves on at least one host. */
  terminal_format: ("text" | "bytes" | "styled")[];
  /** Each terminal host the daemon has and the formats it serves there, e.g. {tmux: [text, bytes], orca: [text]}. Absent: text on the one host it has. */
  terminal_hosts?: { [key: string]: string[] };
  /** The daemon serves the transcript topic and transcript.events. Absent means false: a client then watches the terminal. */
  transcript?: boolean;
};

export type Listener = {
  enabled: boolean;
  /** loopback, lan, tailscale or an address. */
  bind: string;
  /** Always an encrypted WebSocket with a certificate; `none` only on loopback. A Tailscale address is just a bind address. */
  encryption: "tls" | "none";
  fingerprint: string | null;
};

export type HistoryPage = {
  first: number;
  lines: Row[];
  more: boolean;
};

/** Chosen by the caller; unique per intended action. */
export type RequestId = string;

/** What task.done must be given: sha, verdict, decision or report (default; decision when the owner is human). */
export type Expects = "sha" | "verdict" | "decision" | "report";

/** What the proposal changes, by kind (hl hr). Every kind needs name. */
export type HrArgs = {
  /** The agent the proposal is about (the new agent's name for add; a team's manager for team). */
  name: string;
  team?: string;
  repo?: string;
  model?: string;
  effort?: string;
  vendor?: string;
  /** rename: the new name; link, unlink: the other end. */
  to?: string;
  response?: string;
  relation?: string;
  /** team: each a suffix (`sea` is `name.sea`) or a full name. */
  members?: string[];
  /** team: repo by member. */
  repos?: { [key: string]: string };
  /** settings: agent setting keys as AgentSettingValues names them. context: whole new file texts keyed `ROLE.md` (at most 1024 bytes) or `base.md` (at most 2048 bytes); empty text, control characters other than tab and newline, or a file over its budget is bad_request. */
  sets?: { [key: string]: string };
};

export type StandingAnswer = {
  /** trust: a folder trusted for every session; dialog: a rule that answers a vendor's dialog. */
  kind: "trust" | "dialog";
  /** What answer.remove takes: [folder] for trust, [vendor, title, label] for dialog. */
  key: string[];
  text: string;
};

/** An agent's settings, every value a string as `hl agent set <agent> key=value` takes it. Keys an agent may change on itself: model, effort, context_k, handoff_note. Every other key is the founder's (an agent proposes it with hr.propose kind settings). */
export type AgentSettingValues = {
  model?: string;
  effort?: "low" | "medium" | "high" | "xhigh" | "max";
  weight?: "light" | "heavy";
  repo?: string;
  skip_permissions?: "on" | "off";
  /** custom is read-only: set in a terminal. */
  browser?: "off" | "chrome" | "playwright" | "custom";
  /** Space-separated origins. */
  browser_sites?: string;
  /** Handoff threshold in thousands of tokens, a whole number. */
  context_k?: string;
  /** Idle close in whole minutes, 0 never, empty follows the global park_timeout_s. */
  idle_close?: string;
  /** default follows the global switch. */
  handoff_note?: "default" | "on" | "off";
  /** Why a key, not an is_off/back_on pair: it is already an agent setting (agents.sandbox_on, `hl agent set sandbox=on`), read in AgentDetail.settings and written the same way as every other key. `on` takes effect at once (the key and the grant go now; a running session restarts on the next idle sweep). `off` needs the founder's grant. */
  sandbox?: "on" | "off";
  /** Permission mode; bypass only with skip_permissions granted. */
  mode?: "default" | "auto" | "acceptEdits" | "plan" | "bypass";
};

/** Every global setting by its config.yaml name, with its default; every one is the founder's to set. Per-account keys (label, cap, usage recovery, cycle) are account.set; per-agent keys are agent.set and schedule.*; a key marked Per agent also takes `agent` in settings.set. */
export type SettingsValues = {
  /** Default cap: most agents mid-turn at once on an account with no cap of its own. */
  active_cap?: number;
  /** Idle close, seconds; 0 never. The UI shows whole minutes. Per agent with `agent`. */
  park_timeout_s?: number;
  /** Agents write a note for the next session before a close or move. */
  handoff_note?: boolean;
  /** Answer dialogs by the standing rules by itself. Retired in a daemon that answers no dialog (x-dialogs): ignored. */
  auto_accept_dialogs?: boolean;
  /** Resume the old session even after its cache went cold. Per agent with `agent`. */
  ignore_cache_expiry?: boolean;
  /** The daemon restarts itself when its own code changed (hl dev-mode). */
  dev_mode?: boolean;
  /** Seconds of nothing shown before an idle agent with background work is told to finish or stop it; default follows park_timeout_s. Per agent with `agent`. */
  bg_notice_s?: number;
  /** Seconds after that notice before the work is stopped. Per agent. */
  bg_grace_s?: number;
  /** How often an agent with background work is asked which to stop. Per agent. */
  bg_check_s?: number;
  /** Seconds after the first waiting result before the agent is woken once with a digest. Per agent. */
  notice_digest_s?: number;
  /** Seconds an unasked running agent may go before it is asked. */
  unasked_running_s?: number;
  /** Backstop for a stop-wait. */
  stop_wait_backstop_s?: number;
  /** Seconds of silence after which background work counts as stale. */
  stale_background_s?: number;
  /** Input price per million tokens by model-name pattern, laid over the built-in table; an entry that is not a positive number is ignored. */
  token_prices?: { [key: string]: number };
  /** NEW, no code today: seconds a person or device may hold a terminal's input without typing before the daemon takes it back (see terminal `holder` notice). */
  input_hold_idle_s?: number;
};

/** settings.set's changes: any of SettingsValues' keys; null removes one. */
export type SettingsChanges = {
  /** Default cap: most agents mid-turn at once on an account with no cap of its own. null removes the key (back to the default). */
  active_cap?: number | null;
  /** Idle close, seconds; 0 never. The UI shows whole minutes. Per agent with `agent`. null removes the key (back to the default). */
  park_timeout_s?: number | null;
  /** Agents write a note for the next session before a close or move. null removes the key (back to the default). */
  handoff_note?: boolean | null;
  /** Answer dialogs by the standing rules by itself. null removes the key (back to the default). Retired in a daemon that answers no dialog (x-dialogs): ignored. */
  auto_accept_dialogs?: boolean | null;
  /** Resume the old session even after its cache went cold. Per agent with `agent`. null removes the key (back to the default). */
  ignore_cache_expiry?: boolean | null;
  /** The daemon restarts itself when its own code changed (hl dev-mode). null removes the key (back to the default). */
  dev_mode?: boolean | null;
  /** Seconds of nothing shown before an idle agent with background work is told to finish or stop it; default follows park_timeout_s. Per agent with `agent`. null removes the key (back to the default). */
  bg_notice_s?: number | null;
  /** Seconds after that notice before the work is stopped. Per agent. null removes the key (back to the default). */
  bg_grace_s?: number | null;
  /** How often an agent with background work is asked which to stop. Per agent. null removes the key (back to the default). */
  bg_check_s?: number | null;
  /** Seconds after the first waiting result before the agent is woken once with a digest. Per agent. null removes the key (back to the default). */
  notice_digest_s?: number | null;
  /** Seconds an unasked running agent may go before it is asked. null removes the key (back to the default). */
  unasked_running_s?: number | null;
  /** Backstop for a stop-wait. null removes the key (back to the default). */
  stop_wait_backstop_s?: number | null;
  /** Seconds of silence after which background work counts as stale. null removes the key (back to the default). */
  stale_background_s?: number | null;
  /** Input price per million tokens by model-name pattern, laid over the built-in table; an entry that is not a positive number is ignored. null removes the key (back to the default). */
  token_prices?: { [key: string]: number } | null;
  /** NEW, no code today: seconds a person or device may hold a terminal's input without typing before the daemon takes it back (see terminal `holder` notice). null removes the key (back to the default). */
  input_hold_idle_s?: number | null;
};

/** One method as a daemon lists it: its role and whether this caller may call it. */
export type MethodInfo = {
  name: string;
  since: number;
  role: Role;
  allowed: boolean;
};

export type Outlook = {
  range: "12h" | "24h" | "3d" | "7d" | "14d" | "30d";
  now: Timestamp;
  /** How many accounts have a reading. */
  accounts: number;
  /** What usage samples recorded, oldest first. */
  past: {
    at: Timestamp;
    left_pct: number | null;
  }[];
  /** A guess: each account keeps its pace of the last day, and a week that resets starts again from nothing used. Kept apart from `past`, never joined to it. */
  future: {
    at: Timestamp;
    left_pct: number | null;
  }[];
  runway: {
    account: string;
    runs_out_in_seconds: number | null;
    week_resets_at: Timestamp | null;
  }[];
  /** Oldest first; a day with no calls is absent. */
  cost_per_day: {
    /** Local calendar day, YYYY-MM-DD. */
    day: string;
    /** List-price cost in US dollars of each agent that day. */
    agents: { [key: string]: number };
  }[];
  /** What each agent used over the last `range` (the span of `past`), largest cost first, ties by name. A client groups agents by the first part of the dotted name; a group's numbers are the sums of its members' (est_pct null if any member's is). Absent from a daemon that does not send it. */
  agent_use?: {
    agent: string;
    /** Tokens the agent used in the span. */
    tokens: number;
    /** Its cost in US dollars at list price in the span, from the token log. */
    cost: number;
    /** Its cost as a fraction (0 to 1) of everyone's cost in the span; 0 when nothing was spent. */
    share: number;
    /** An estimate of the points of the combined week the agent used in the span: its cost share of each account times the points that account used, averaged over the accounts. An estimate, because what the plan charges is not what the price list says. Null when no account has a reading. */
    est_pct: number | null;
    /** Its cost share of each account in the span, by account name (a fraction, 0 to 1). An account on which nothing at all was spent is absent; one on which others spent but none of this agent's tokens was priced is 0. */
    fracs: { [key: string]: number };
  }[];
  /** Only with `agent`: the points of the combined week the pick had used by each time of `past`, one number per `past` entry in the same order: its cost share of each account times what that account had used so far, averaged over the accounts. Empty when the pick used nothing in the span or is not an agent or group. Absent from a daemon that does not send it. */
  overlay?: number[];
};

export type TerminalBytesSnapshot = {
  terminal: string;
  format: "bytes";
  /** The last notice of this attach the screen includes; the next notice has seq+1. */
  seq: number;
  cols: number;
  rows: number;
  /** Base64. Written unchanged to a freshly reset terminal of cols by rows, it paints the screen, the modes and the cursor. */
  data: string;
  /** The pane is on the alternate screen; data begins by switching to it. */
  alt_screen: boolean;
  /** 0-based column and row; also where data leaves the cursor. */
  cursor: {
    x: number;
    y: number;
  };
  cursor_visible: boolean;
  history: HistoryBytesPage;
  /** The first of holders; kept for clients written to the single value. */
  holder: InputHolder;
  /** Everyone who holds the input, oldest first; one in v1. */
  holders: InputHolder[];
};

export type HistoryBytesPage = {
  /** Absolute index of the first line, as in HistoryPage. */
  first: number;
  lines: string[];
  more: boolean;
};

export type ChatRequestItem = {
  type: "request" | "task_event" | "session";
  /** Where it sits in time: a request where its agent read it (landed_at) or else when it was made. */
  at: Timestamp;
  /** request only. */
  message?: ChatMessage;
  dismissal?: {
    by: string | null;
    reason: string | null;
  } | null;
  task_state?: string | null;
  /** task_event only. */
  task?: number;
  /** task_event only: the task's title. */
  title?: string;
  /** task_event only: who did it. */
  who?: string;
  /** task_event only: `queued -> doing`, `handed to X`, `assigned to X`, `title changed`, `priority 2 -> 1`. An open set: a client draws a word it does not know as text. */
  change?: string;
  /** task_event only: the text typed with a handoff, else empty. Text from an agent. */
  note?: string;
  request?: number | null;
  /** session only: why a new conversation started (restart, wake, fresh close, move, handoff, launch). An open set. */
  cause?: string;
  handoff?: string | null;
};

/** An xterm palette index (0-15 are the client's theme ANSI colours, 16-255 the fixed cube and greys) or a true colour as lower-case `#rrggbb`. A colour the cell does not have (the default) is left out, never sent as a number. */
export type CellColor = number | string;

/** Only what differs from the default cell: a flag is sent as true or left out, a colour is left out for the default. `inverse` is sent as the flag, not applied: the client swaps the foreground and background it draws, so default colours swap too. A client ignores a key it does not know. */
export type SpanStyle = {
  fg?: CellColor;
  bg?: CellColor;
  bold?: boolean;
  dim?: boolean;
  italic?: boolean;
  underline?: boolean;
  inverse?: boolean;
  strike?: boolean;
};

export type ScheduleError = {
  target: AgentName;
  /** Why the agent's `schedules` value cannot be read, as `hl schedule list` says it. */
  error: string;
};

/** One thing a structured session did, in order. Exactly the field named like `kind` is present besides the common ones (`text` for kind text). Events are never changed after they are sent. */
export type TranscriptEvent = {
  agent: AgentName;
  /** The session id the event belongs to. A new session (a fresh start, not a resume) changes it; seq goes on. */
  session: string;
  /** Per agent, starting at 1, up by exactly 1 with each event, across sessions, never reused. A client ignores an event whose seq is not above the last it applied and reads transcript.events on a hole. */
  seq: number;
  turn: string | null;
  at: Timestamp;
  /** Open set: a client skips a kind it does not know. */
  kind: "turn_start" | "text" | "tool_use" | "tool_result" | "permission" | "permission_answer" | "turn_end" | "error";
  item: number | null;
  /** kind text: one whole assistant text block (no partial deltas in v1). */
  text?: string;
  /** kind turn_start: a message began a turn. */
  turn_start?: {
    /** The message uuid. */
    message: string;
    from: string | null;
    text: string;
  };
  /** kind tool_use: the model called a tool. */
  tool_use?: {
    /** The tool call's id; its tool_result and its permission carry it. */
    id: string;
    tool: string;
    /** The tool's input as the model gave it. */
    input: { [key: string]: unknown };
  };
  /** kind tool_result: what the tool returned. */
  tool_result?: {
    tool_use_id: string;
    ok: boolean;
    /** At most 64 KiB; longer output is cut and `truncated` is true. */
    text: string;
    truncated: boolean;
    /** Present when truncated: the lines cut from the end of the output. */
    dropped_lines?: number;
  };
  /** kind permission: a person is asked. Answered by prompt.answer, with choice allow or deny. */
  permission?: {
    tool_use_id: string;
    /** kind permission; its id is tool_use_id; options are allow and deny; `reason` is the CLI's decision_reason. */
    prompt: Prompt;
  };
  /** kind permission_answer: a tool call was allowed or denied. A call a policy decides has this event alone, with no permission before it. */
  permission_answer?: {
    tool_use_id: string;
    behavior: "allow" | "deny";
    /** person: prompt.answer. policy: the rule named in `rule`. cancel: the CLI withdrew the prompt. closed: the session closed or no handler answered (fail closed: deny). */
    by: "person" | "policy" | "cancel" | "closed";
    rule: string | null;
    message: string | null;
  };
  /** kind turn_end: the turn is over. */
  turn_end?: {
    reason: "done" | "interrupted" | "error";
    cost_usd: number;
    duration_ms: number;
    usage: {
      input: number;
      output: number;
      cache_read: number;
      cache_write: number;
    };
  };
  /** kind error: the session or the CLI failed; a turn_end follows if a turn was running. */
  error?: {
    code: string;
    text: string;
  };
  /** kind turn_start: the images the message that began the turn carried. Absent when none. */
  attachments?: Attachment[];
};

export type TranscriptEvents = {
  events: TranscriptEvent[];
  /** There are more in the direction asked. */
  more: boolean;
  /** The oldest seq the daemon still keeps for the agent; 0 when it has none. */
  first_seq: number;
  /** The newest seq of the agent; 0 when it has none. */
  last_seq: number;
  /** since_seq was older than first_seq - 1: the read starts at first_seq, and what lay between is gone. */
  gap: boolean;
  working: Working;
};

/** Where a message to a structured agent is on its way into the CLI. Only for an agent whose AgentView.structured is true; see x-delivery. */
export type Receipt = {
  /** queued: held by the daemon behind a running turn, not written to the CLI. sent: written to the CLI, not yet echoed. confirmed: the CLI echoed the message's uuid. unknown: written, and the daemon or the CLI ended before the echo; never written again. */
  state: "queued" | "sent" | "confirmed" | "unknown";
  /** The message's uuid, derived by the daemon from the request_id; the id the transcript stream's turn_start carries. Read it, do not compute it. */
  uuid: string;
  /** The seq of the transcript event that carries this uuid, when there is one; absent otherwise (a message that joined a running turn has no turn_start of its own). Best effort; a client does not wait for it. */
  seq?: number;
};

export type TerminalOpening = {
  agent: AgentName;
  /** The terminal handle, as terminal.attach takes it. */
  terminal: string;
  /** tmux or orca; open set. */
  host: string;
  attach: {
    argv: string[];
  } | null;
  focused: boolean;
  local: boolean;
};

export type NeedsYou = {
  /** permission: a tool or a folder waits for a yes. question: the agent asked the person something. usage: the session limit's choice. dialog: any other, and any the daemon does not know: unknown is a person. */
  kind: "permission" | "question" | "usage" | "dialog";
  since: Timestamp;
  /** One line, from the hook or the session file; never read from the screen. */
  text: string | null;
  /** hook: PermissionRequest or Notification. session: the session's own file (sessions/<pid>.json status waiting and waitingFor). */
  source: "hook" | "session";
};

/** Chosen by the daemon when an upload completes. */
export type AttachmentId = string;

/** An image the person attached to a message. The file lives on the daemon's machine; a client draws it with attachment.get. */
export type Attachment = {
  id: AttachmentId;
  /** The file name the client gave, with no folder in it (the daemon cuts one off). Shown to the person; never used as a path. */
  name: string;
  mime: "image/png" | "image/jpeg" | "image/gif" | "image/webp";
  bytes: number;
};

export interface Methods {
  "hello": { params: {
  /** name/version of the caller. */
  client: string;
  /** The protocol the client will speak. */
  protocol: number;
  /** Device token; network listener only. */
  token?: string;
  /** The founder key, from the founder's own key file; local socket only. Wrong key: refused, gate `role`. */
  key?: string;
}; result: {
    /** Newest protocol the daemon speaks. */
    protocol: number;
    /** Oldest client protocol it still serves. */
    min_client: number;
    /** Loaded git sha. */
    daemon: string;
    schema: number;
    role: Role;
    caller: Caller;
    capabilities: Capabilities;
    /** How this connection is protected. */
    transport: "local" | "tls";
    methods: MethodInfo[];
  } };
  "whoami": { params: { [key: string]: unknown }; result: {
    caller: Caller;
    role: Role;
    /** The same list hello gives, with allowed flags for this caller. */
    methods: MethodInfo[];
  } };
  "subscribe": { params: {
  topics: Topic[];
  /** Last cursor seen. Absent: live only. A cursor older than the daemon keeps, or with more than 5000 subscribed events after it, is answered by a `gap` notice (reason retention) instead of a replay; the connection stays open. Up to 5000 events behind, every missed event is replayed in full, in cursor order, before any live one. */
  since?: number;
  /** Only events of this agent, plus events that carry no agent. Meant for the chat topic. */
  agent?: AgentName;
}; result: {
    /** The newest cursor at subscribe time. */
    cursor: number;
  } };
  "unsubscribe": { params: {
  topics: Topic[];
}; result: { [key: string]: unknown } };
  "daemon.status": { params: { [key: string]: unknown }; result: DaemonStatus };
  "events.feed": { params: {
  since?: number;
  topics?: Topic[];
  limit?: number;
}; result: {
    events: Event[];
    cursor: number;
    /** True when `since` is older than retention; refetch snapshots. */
    gap: boolean;
  } };
  "operation.get": { params: {
  operation: string;
}; result: Operation };
  "agents.list": { params: {
  /** Add the status-line fields of each agent (started, has_repo, has_terminal, screen_word, screen_detail, unreached, task_waits, wake_wait_reason), read now and not from the stored view. Default false. */
  status?: boolean;
}; result: {
    agents: AgentView[];
  } };
  "agent.get": { params: {
  agent: AgentName;
}; result: AgentDetail };
  "agent.timeline": { params: {
  agent: AgentName;
  /** Return rows older than this id. */
  before?: number;
  /** Default 50, at most 200. */
  limit?: number;
}; result: {
    items: TimelineEntry[];
    more: boolean;
  } };
  "agent.options": { params: { [key: string]: unknown }; result: AgentOptions };
  "agent.set": { params: {
  agent: AgentName;
  values: AgentSettingValues;
  speaker?: string;
  request_id: RequestId;
}; result: Accepted };
  "agent.fold": { params: {
  agent: AgentName;
  request_id: RequestId;
}; result: Accepted };
  "agent.move": { params: {
  agent: AgentName;
  account: string;
  request_id: RequestId;
}; result: Accepted };
  "agent.unstall": { params: {
  agent: AgentName;
  request_id: RequestId;
}; result: Accepted };
  "agent.create": { params: {
  name: AgentName;
  /** A free string today. */
  role: string;
  /** A directory, as repos.list gives it in path. An unknown one is not_found, what repo. */
  repo: string;
  /** The branch to start from. The worktree takes the side, local or origin/, that leads, and the operation fails when they have diverged. */
  base?: string;
  model?: string;
  effort?: "low" | "medium" | "high" | "xhigh" | "max";
  parent?: AgentName;
  /** Not served: bad_request, field account, never dropped. A new agent's account is chosen when it starts; a client offers no account picker until a daemon serves this. */
  account?: string;
  request_id: RequestId;
}; result: OperationStarted };
  "session.start": { params: {
  agent: AgentName;
  /** The Go daemon does not place sessions on a named account yet: it answers bad_request, field account, never drops it. A client offers no account picker until then. */
  account?: string;
  repo?: string;
  base?: string;
  request_id: RequestId;
}; result: Accepted };
  "session.wake": { params: {
  agent: AgentName;
  request_id: RequestId;
}; result: Accepted };
  "session.stop": { params: {
  agent: AgentName;
  mode: "exit" | "close" | "kill";
  /** close only: the next start is a fresh session. */
  fresh?: boolean;
  request_id: RequestId;
}; result: Accepted };
  "session.restart": { params: {
  agent: AgentName;
  fresh?: boolean;
  force?: boolean;
  request_id: RequestId;
}; result: Accepted };
  "session.park": { params: {
  agent: AgentName;
  request_id: RequestId;
}; result: Accepted };
  "session.input": { params: {
  agent: AgentName;
  text: string;
  /** Default true. */
  enter?: boolean;
  request_id: RequestId;
}; result: Accepted };
  "session.keys": { params: {
  agent: AgentName;
  /** esc, enter, up, down, tab, shift-tab, ctrl-c, 0-9. */
  keys: string[];
  request_id: RequestId;
}; result: Accepted };
  "session.type": { params: {
  agent: AgentName;
  /** Printable characters and tab; no other control characters (those are session.keys). */
  text: string;
  request_id: RequestId;
}; result: Accepted };
  "session.interrupt": { params: {
  agent: AgentName;
  request_id: RequestId;
}; result: Accepted };
  "prompt.list": { params: {
  agent: AgentName;
}; result: {
    prompts: Prompt[];
  } };
  "prompt.answer": { params: {
  agent: AgentName;
  prompt: string;
  /** A PromptOption.key, or {text} for the free-text answer to a dialog that asks for one; a text answer passes the same gates and the prompt check. An array only answers a `multi` prompt. */
  choice: string | string[] | {
    text: string;
  };
  request_id: RequestId;
}; result: Accepted };
  "input.take": { params: {
  agent: AgentName;
  request_id: RequestId;
}; result: Accepted & {
    holder: InputHolder;
  } };
  "input.release": { params: {
  agent: AgentName;
  request_id: RequestId;
}; result: Accepted };
  "chat.messages": { params: {
  agent: AgentName;
  /** mine: person and this agent; all: everything it sent or received. */
  scope: "mine" | "all";
  before?: number;
  since?: number;
  limit?: number;
}; result: {
    messages: ChatMessage[];
    more: boolean;
  } };
  "chat.requests": { params: {
  agent: AgentName;
  /** mine: person and this agent; all: everything it sent or received. */
  scope: "mine" | "all";
  /** Request id: read the page just older than it. */
  before?: number;
  /** Request id: read every request from it on (no `more`); how a window already drawn is brought up to date. Not with `before`. */
  since?: number;
  /** Requests in a page; default 10. */
  limit?: number;
}; result: {
    items: ChatRequestItem[];
    /** Older requests remain (never true with `since`). */
    more: boolean;
    /** Opaque: equal on two reads when nothing in the chat changed between them (a request made, answered, closed, changed, delivered or landed, a task event, a session start). Cheap to compare; a client that sees it change reads again. */
    version: string;
    /** The newest stream cursor when this was read: subscribe with since=cursor and topics [chat] to hear what changes after it. */
    cursor: number;
  } };
  "chat.transcript": { params: {
  agent: AgentName;
  before?: number;
  since?: number;
  limit?: number;
}; result: {
    items: TranscriptItem[];
    more: boolean;
    working: Working;
  } };
  "transcript.events": { params: {
  agent: AgentName;
  /** The events after this seq, oldest first. 0 or absent with no before_seq: the newest `limit` events, oldest first. */
  since_seq?: number;
  /** The page just older than this seq, oldest first. Not with since_seq. */
  before_seq?: number;
  /** 1 to 500, default 200. */
  limit?: number;
}; result: TranscriptEvents };
  "chat.locate": { params: {
  request: number;
  /** Optional: the daemon finds the conversation from the message itself. */
  agent?: AgentName;
  /** Page size, default 50. */
  limit?: number;
}; result: {
    /** The agent whose chat shows it. */
    agent: AgentName;
    /** mine: person and this agent; all: everything it sent or received. */
    scope: "mine" | "all";
    message: ChatMessage;
    /** A page, newest first, containing message; continue with chat.messages before (older) or since (newer). */
    messages: ChatMessage[];
    more_older: boolean;
    more_newer: boolean;
  } };
  "agent.strip": { params: {
  agent: AgentName;
}; result: {
    agent: AgentName;
    model: string | null;
    account: string | null;
    /** Percent of the context window used, as far as the session's own report is trusted. */
    ctx_pct: number | null;
    /** Tokens in use. */
    ctx_used: number | null;
    /** The window the model has, with the agent's own setting applied. */
    ctx_limit: number | null;
    /** Seconds until the prompt cache lapses; negative once it has; null with nothing to measure from. */
    cache_left_s: number | null;
    /** The agent's state as the screens draw it (`tui.screen_state`), not the wire `state`; null when it never started. */
    screen_word: ("RUNNING" | "IDLE" | "PENDING" | "MOVING" | "DIALOG" | "FATAL") | null;
    /** The text drawn beside screen_word. */
    screen_detail: string;
  } };
  "chat.refs": { params: {
  agent: AgentName;
}; result: {
    /** Up to 30, newest first: every request the agent sent or received that was not withdrawn or turned away. */
    requests: {
      request: number;
      from: string;
      to: string;
      /** The first line of the body, at most 60 characters. */
      line: string;
    }[];
  } };
  "chat.search": { params: {
  agent?: AgentName;
  query: string;
  /** mine: person and this agent; all: everything it sent or received. */
  scope?: "mine" | "all";
  limit?: number;
}; result: {
    messages: ChatMessage[];
  } };
  "chat.send": { params: {
  agent: AgentName;
  body: string;
  task?: {
    title: string;
    time_box?: Duration;
  };
  /** The message this one replies to: a reference, for any message whatever it asked of the person. The message is only a say to the agent with the parent link; it answers, settles or changes nothing about the parent. Not with task. A message that does not exist is not_found; one that agent is not on, as its requester or its target, is refused (gate state); a message between two other agents is replied to by a say to the one named by agent. request in the result is the new message. */
  parent?: number;
  /** Images from attachment.put to send with this message, drawn in this order. Then body may be empty. An id that is unknown, expired or already sent is bad_request, field attachments. Not with task (bad_request). */
  attachments?: AttachmentId[];
  /** A reply of the agent's own that this message answers: the `id` of an item of kind reply that chat.transcript lists for this agent (a structured session's text block, whose live event carries the same id as `item`, or the answer to a request). The agent's turn text has no message number, so it cannot be `parent`; the two do not go together, nor does this with task (bad_request, field parent_item). It may go with attachments. An id that is no item of this agent's transcript, or one of another agent's, is not_found (what: item); one that is not a reply (a tool call, a prompt, a task event, a session) is bad_request. */
  parent_item?: number;
  /** For a structured agent whose turn is running: now joins the running turn, queue (the default) is held by the daemon and written when the turn ends, one message per turn. With no turn running they are the same. A terminal agent accepts the field and ignores it. Another word is bad_request. */
  delivery?: "now" | "queue";
  request_id: RequestId;
}; result: Accepted & {
    request: number;
    receipt?: Receipt;
  } };
  "chat.send_now": { params: {
  /** A ChatMessage.id still in state queued. */
  request: number;
  request_id: RequestId;
}; result: Accepted & {
    receipt?: Receipt;
  } };
  "chat.dismiss": { params: {
  request: number;
  request_id: RequestId;
}; result: Accepted };
  "chat.cancel": { params: {
  request: number;
  /** Needed once the agent has been told the message. Without it the call is refused (gate confirm) and error data.plan holds what cancelling would do and who is told; send it again with confirm true to do it. */
  confirm?: boolean;
  request_id: RequestId;
}; result: Accepted };
  "chat.seen": { params: {
  agent: AgentName;
  /** The highest message number seen. A lower number than before changes nothing: what arrives later has a higher one, so nothing unseen is hidden. */
  up_to: number;
  request_id: RequestId;
}; result: Accepted };
  "attachment.put": { params: {
  name: string;
  mime: "image/png" | "image/jpeg" | "image/gif" | "image/webp";
  /** The whole file's size, the same in every chunk of one upload. */
  bytes: number;
  /** Where this chunk starts. Default 0. */
  offset?: number;
  /** From the first reply. Absent on the chunk at offset 0 that opens the upload. */
  upload?: string;
  /** The chunk, at most 512 KiB of file. */
  data: string;
  request_id: RequestId;
}; result: Accepted & {
    upload: string;
    /** Bytes held so far. */
    received: number;
    /** Only on the chunk that completes the file. */
    attachment?: Attachment;
  } };
  "attachment.get": { params: {
  id: AttachmentId;
  /** A copy with its longest side at most 320 px, as PNG or JPEG, in one reply (at most 256 KiB). Default false. */
  thumb?: boolean;
  /** Where to read the original from. Default 0. Not with thumb. */
  offset?: number;
}; result: {
    mime: "image/png" | "image/jpeg" | "image/gif" | "image/webp";
    /** The size of the whole original, or of the thumbnail when thumb. */
    bytes: number;
    offset: number;
    /** At most 512 KiB of file; the rest is another call at offset + the length read. */
    data: string;
    /** True when this reply reaches the end. */
    done: boolean;
  } };
  "terminal.attach": { params: {
  agent?: AgentName;
  /** Handle; give agent or terminal, not both. */
  terminal?: string;
  /** History lines to include; default 100. */
  history?: number;
  /** Default text. bytes is served on tmux-hosted terminals; styled on a host whose screen read carries cell attributes; see x-terminal-bytes and x-terminal-styled. */
  format?: "text" | "bytes" | "styled";
}; result: TerminalSnapshot | TerminalBytesSnapshot };
  "terminal.history": { params: {
  terminal: string;
  before: number;
  limit: number;
  /** Default text. bytes is served on tmux-hosted terminals; styled on a host whose screen read carries cell attributes; see x-terminal-bytes and x-terminal-styled. */
  format?: "text" | "bytes" | "styled";
}; result: HistoryPage | HistoryBytesPage };
  "terminal.resize": { params: {
  terminal: string;
  cols: number;
  rows: number;
  request_id: RequestId;
}; result: Accepted };
  "terminal.input": { params: {
  terminal: string;
  /** Base64 of the bytes a terminal sends for the keys (xterm's own: ESC [ A for up, 0x03 for ctrl-c, a bracketed paste with its markers); 1 to 16384 bytes decoded, else bad_request. */
  data: string;
  request_id: RequestId;
}; result: Accepted };
  "terminal.close": { params: {
  agent: AgentName;
  request_id: RequestId;
}; result: Accepted };
  "terminal.open": { params: {
  agent: AgentName;
}; result: TerminalOpening };
  "terminal.detach": { params: {
  terminal: string;
  /** With it, ends only that format's attach of the terminal on this connection; without it, every attach of that terminal on the connection. Neither is an error when there is nothing to end; any other word is bad_request naming `format`. */
  format?: "text" | "bytes" | "styled";
}; result: { [key: string]: unknown } };
  "inbox.list": { params: {
  unread?: boolean;
  kind?: string;
  agent?: AgentName;
  /** Return rows older than this id. */
  before?: number;
  /** Default 50, at most 200. */
  limit?: number;
  /** Hold the call up to this many seconds while no item matches the other params (unread, kind, agent, before; read items count unless unread is true), and return when one arrives (an empty list on timeout). Default 0, at most 600; the call is in flight and the connection is not idle. */
  wait_s?: number;
  /** An agent caller: add each item's how_to_close and the result's more_waiting, as `hl inbox` prints them. Default false; ignored for the founder. */
  queue?: boolean;
}; result: {
    items: InboxItem[];
    more: boolean;
    /** Only with `queue: true`, for an agent caller: how many requests wait in its queue behind the one `hl inbox` shows (the queue depth less one). */
    more_waiting?: number;
  } };
  "inbox.get": { params: {
  id: number;
  /** Hold the call up to this many seconds until the request has been answered. Default 0, at most 600. */
  wait_s?: number;
}; result: InboxItem };
  "inbox.mark_read": { params: {
  ids: number[];
  request_id: RequestId;
}; result: Accepted };
  "inbox.answer": { params: {
  id: number;
  option?: string;
  text?: string;
  request_id: RequestId;
}; result: Accepted };
  "inbox.reply": { params: {
  id: number;
  text: string;
  request_id: RequestId;
}; result: Accepted };
  "tasks.list": { params: {
  owner?: string;
  from?: string;
  state?: string;
  parent?: number;
  /** Return rows older than this id. */
  before?: number;
  /** Default 50, at most 200. */
  limit?: number;
  /** Done and discarded tasks too. */
  all?: boolean;
  /** Every team's tasks, not only the caller's and its reports'. */
  everyone?: boolean;
  /** Also count, over the last 30 days, the requests that ended with no reason and how tasks were closed (the `reason_gaps` of the result). It costs seconds on a large database: a screen that polls never asks. */
  reason_gaps?: boolean;
}; result: {
    items: Task[];
    more: boolean;
    /** Present only when asked for (`reason_gaps`: true). How many requests ended in the last 30 days with no reason, and how tasks were closed in that time (by sha, by request, by reason): what hl task list prints under the list. */
    reason_gaps?: {
      retirements: number;
      retirements_blank: number;
      closures_sha: number;
      closures_request: number;
      closures_reason: number;
    };
  } };
  "task.get": { params: {
  task: number;
}; result: TaskDetail };
  "task.shot": { params: {
  task: number;
  /** The screenshot's number in the task's result, from 1. */
  n: number;
  /** Where the chunk starts in the file; 0 when omitted. */
  offset?: number;
}; result: {
    mime: "image/png";
    bytes: number;
    offset: number;
    /** The chunk, in base64: at most 512 KiB of file. */
    data: string;
    done: boolean;
  } };
  "task.new": { params: {
  owner: string;
  /** Who the task answers to: told when it ends, and may edit or reassign it. Default: the caller. */
  reporter?: string;
  title: string;
  body?: string;
  time_box: Duration;
  relation?: string;
  response?: string;
  parent?: number;
  /** Cannot be worked until this task closes. */
  blocked_by?: number;
  /** 1 high, 2 default, 3 low (written P1-P3). Absent means 2. */
  priority?: 1 | 2 | 3;
  expects?: Expects;
  /** Also answer the founder's message with text, where {task} is this task's real number. */
  reply?: {
    message: number;
    /** {task} is replaced by the new task's number. */
    text: string;
  };
  /** The task shows something on a screen. A label only: task.done takes a screenshot (shots) but never requires one. */
  ui?: boolean;
  request_id: RequestId;
}; result: Accepted & {
    task: number;
  } };
  "task.handoff": { params: {
  task: number;
  to: string;
  note: string;
  request_id: RequestId;
}; result: Accepted };
  "task.done": { params: {
  task: number;
  /** A merge sha on main, or a reason in words. */
  evidence?: string;
  /** A request where the result was already reported. */
  request?: number;
  /** The repo whose main a sha is checked against. */
  repo?: string;
  /** Record evidence as words even if it looks like hex. */
  reason?: boolean;
  /** The sha the work was done at; it must be a commit in repo (it need not be on main). The result says whether main reaches it. A sha given as evidence is a commit on main already. */
  commit?: string;
  /** Absolute paths of PNG files, at most 10 MiB each. An agent names files under its worktree, its folder in the daemon's home or the scratch folder; the founder any file. The daemon copies them under its home (shots/T<n>/<k>.png, outside any repo) once the task's end has committed, and serves them with task.shot. */
  shots?: string[];
  request_id: RequestId;
}; result: Accepted };
  "task.reopen": { params: {
  task: number;
  note?: string;
  request_id: RequestId;
}; result: Accepted };
  "task.progress": { params: {
  task?: number;
  note: string;
  request_id: RequestId;
}; result: Accepted };
  "progress.get": { params: { [key: string]: unknown }; result: {
    /** The note, or null when none is kept. */
    note: string | null;
  } };
  "task.begin": { params: {
  title: string;
  body?: string;
  /** The founder's message it answers. */
  message: number;
  priority?: 1 | 2 | 3;
  expects?: Expects;
  /** What led to this, in a line. */
  why: string;
  request_id: RequestId;
}; result: Accepted & {
    task: number;
  } };
  "task.start": { params: {
  task: number;
  request_id: RequestId;
}; result: Accepted };
  "task.memo.list": { params: {
  task: number;
}; result: {
    memos: Memo[];
  } };
  "task.memo.add": { params: {
  task: number;
  text: string;
  request_id: RequestId;
}; result: Accepted & {
    memo: number;
  } };
  "task.memo.edit": { params: {
  task: number;
  memo: number;
  text: string;
  request_id: RequestId;
}; result: Accepted };
  "task.memo.delete": { params: {
  task: number;
  memo: number;
  request_id: RequestId;
}; result: Accepted };
  "task.queue": { params: {
  tasks: number[];
  request_id: RequestId;
}; result: Accepted };
  "task.draft": { params: {
  tasks: number[];
  request_id: RequestId;
}; result: Accepted };
  "task.merge": { params: {
  into: number;
  tasks: number[];
  request_id: RequestId;
}; result: Accepted };
  "task.release": { params: {
  task: number;
  to?: "queued" | "draft";
  reason?: string;
  request_id: RequestId;
}; result: Accepted };
  "task.discard": { params: {
  task: number;
  reason: string;
  request_id: RequestId;
}; result: Accepted };
  "task.board": { params: {
  /** Every team's open tasks, not only the caller's and its reports'. */
  everyone?: boolean;
}; result: {
    mine: TaskBoardRow[];
    reports: TaskBoardRow[];
  } };
  "task.withdraw": { params: {
  task: number;
  request_id: RequestId;
}; result: Accepted };
  "task.cancel": { params: {
  task: number;
  reason?: string;
  request_id: RequestId;
}; result: Accepted };
  "task.edit": { params: {
  task: number;
  owner?: string;
  /** A new reporter (who the task answers to); by its opener, its reporter or the founder. */
  reporter?: string;
  title?: string;
  body?: string;
  time_box?: Duration;
  /** Owner only: what the task waits on. */
  waits_on?: string;
  /** 1 high, 2 default, 3 low (written P1-P3). */
  priority?: 1 | 2 | 3;
  /** A task number this one waits on, added in the same transaction as the other parts; a refusal of any part undoes all of them. */
  blocked_by?: number;
  /** A task number this one no longer waits on; clearing the last one lets its owner's current task advance. */
  unblock?: number;
  request_id: RequestId;
}; result: Accepted };
  "task.ask": { params: {
  task: number;
  to: AgentName;
  /** The question; not empty. */
  text: string;
  request_id: RequestId;
}; result: Accepted };
  "say": { params: {
  target: string;
  body: string;
  task?: number;
  /** Why this carries no task. */
  no_task?: string;
  parent?: number;
  request_id: RequestId;
}; result: Accepted };
  "land": { params: {
  sha: string;
  message: string;
  tree?: string;
  /** The merger's housekeeping: whole files by path, written over the merge. Each path must be on main's tools/housekeeping.paths and no file agents read as instructions; at most 1 MiB in all. Anything else needs a PASS. */
  fix?: { [key: string]: string };
  /** Main gets one commit carrying `message`, not the merge. */
  squash?: boolean;
  request_id: RequestId;
}; result: Accepted };
  "land.pass": { params: {
  sha: string;
  request_id: RequestId;
}; result: Accepted };
  "land.authorize": { params: {
  agent: AgentName;
  land_verb: "land" | "land_pass";
  sha: string;
  key: string;
  request_id: RequestId;
}; result: Accepted };
  "hook.record": { params: {
  agent: AgentName;
  kind: string;
  at: Timestamp;
  data: { [key: string]: unknown };
  request_id: RequestId;
}; result: Accepted };
  "hr.propose": { params: {
  kind: "add" | "retire" | "rename" | "link" | "unlink" | "team" | "settings" | "context" | "bundle";
  args: HrArgs;
  why?: string;
  request_id: RequestId;
}; result: Accepted & {
    proposal: number;
  } };
  "hr.withdraw": { params: {
  proposal: number;
  request_id: RequestId;
}; result: Accepted };
  "trust.propose": { params: {
  root: string;
  why?: string;
  request_id: RequestId;
}; result: Accepted & {
    proposal?: string;
  } };
  "trust.list": { params: {
  proposals?: boolean;
}; result: {
    roots: string[];
    /** Empty unless `proposals` is true. State is the store's word (proposed, applied, rejected...). */
    proposals: {
      id: string;
      root: string;
      state: string;
      /** The decision request that asked the founder. */
      request?: number;
      requested_by?: string;
    }[];
    /** The same roots as `roots`, in the same order, with why and who added each. */
    declared: {
      root: string;
      why: string;
      /** An agent's name, or human. */
      added_by: string;
    }[];
  } };
  "trust.remove": { params: {
  root: string;
  request_id: RequestId;
}; result: Accepted };
  "agent.triggers": { params: {
  agent?: AgentName;
}; result: {
    agents: {
      agent: AgentName;
      fired: boolean;
      reason: string;
    }[];
  } };
  "memory.status": { params: {
  agent?: AgentName;
}; result: {
    agents: {
      agent: AgentName;
      bytes: number;
      budget: number;
      entries: number;
      unused: string[];
    }[];
  } };
  "team.get": { params: { [key: string]: unknown }; result: {
    agents: TeamAgent[];
    edges: {
      from: string;
      to: string;
    }[];
  } };
  "hr.list": { params: {
  /** Only the proposals in that state; without it, the list below. */
  state?: string;
}; result: {
    proposals: Proposal[];
  } };
  "hr.decide": { params: {
  proposal: number;
  decision: "approve" | "reject";
  note?: string;
  subject?: string;
  request_id: RequestId;
}; result: Accepted };
  "accounts.list": { params: {
  /** Add each account's agents_assigned, agents_running and agents_unplaced. Default false. */
  roster?: boolean;
  /** Add each account's plan, windows, ctx_pct, age_s, runway, assignable and assign_reason: what `hl account list` prints beside the windows. Default false. */
  usage?: boolean;
}; result: {
    accounts: Account[];
  } };
  "status.pick": { params: { [key: string]: unknown }; result: {
    /** The account to place the next agent on; null when none may take work (fail-closed). */
    picked: string | null;
    candidates: {
      account: string;
      reason: string;
    }[];
    /** One line per agent kind: its price per minute of the 5h window and what the price rests on. */
    agent_costs: string[];
  } };
  "account.set": { params: {
  account: string;
  /** Alias, one plain line; empty clears. */
  label?: string;
  cap?: number;
  recover?: boolean;
  /** The one-month subscription switch (hl account cycle on|off); off keeps the end day. */
  cycle?: boolean;
  /** Last usable day YYYY-MM-DD; a date alone turns the cycle on, as `hl account cycle --end` does; null clears it. */
  cycle_end?: string | null;
  request_id: RequestId;
}; result: Accepted };
  "account.add": { params: {
  /** The new account's name: one plain line, no slash, not starting with a dot. */
  account: string;
  /** The tool the account is for; omitted means the default (claude). */
  vendor?: string;
  request_id: RequestId;
}; result: Accepted & {
    /** The name as registered. */
    account: string;
  } };
  "account.login": { params: {
  account: string;
  request_id: RequestId;
}; result: Accepted & {
    login_id: string;
    state: "waiting";
    /** The authorize URL the claude CLI printed; the person opens it and brings the code back with account.login_code. */
    url: string;
  } };
  "account.login_cancel": { params: {
  login_id: string;
  request_id: RequestId;
}; result: Accepted };
  "account.login_code": { params: {
  login_id: string;
  /** One-time; written to the CLI's stdin and nowhere else. */
  code: string;
  request_id: RequestId;
}; result: Accepted };
  "account.remove": { params: {
  account: string;
  /** Change nothing: answer `plan` and `would_remove`. A dry run needs no `confirm`. */
  dry_run?: boolean;
  /** The account's name, as the founder typed it; required unless dry_run. */
  confirm?: string;
  request_id: RequestId;
}; result: Accepted & {
    /** Dry run only: whether the removal would go ahead. */
    would_remove?: boolean;
    /** Dry run only: what it would do, line by line, or the one line that says why it would refuse. */
    plan?: string[];
  } };
  "repos.list": { params: { [key: string]: unknown }; result: {
    repos: Repo[];
  } };
  "worktrees.list": { params: {
  repo?: string;
  owner?: AgentName;
}; result: {
    worktrees: Worktree[];
  } };
  "worktree.diff": { params: {
  path: string;
  file?: string;
}; result: {
    path: string;
    file: string | null;
    /** Unified diff. */
    diff: string;
    truncated: boolean;
  } };
  "worktree.set_status": { params: {
  path: string;
  status: string;
  request_id: RequestId;
}; result: Accepted };
  "schedules.list": { params: { [key: string]: unknown }; result: {
    schedules: Schedule[];
    /** Present only when an agent's `schedules` value cannot be read: that agent, and why. */
    errors?: ScheduleError[];
  } };
  "schedule.add": { params: {
  target: AgentName;
  cron: string;
  message: string;
  request_id: RequestId;
}; result: Accepted & {
    schedule: number;
  } };
  "schedule.edit": { params: {
  schedule: number;
  target?: AgentName;
  cron?: string;
  message?: string;
  request_id: RequestId;
}; result: Accepted };
  "schedule.remove": { params: {
  schedule: number;
  request_id: RequestId;
}; result: Accepted };
  "stats.get": { params: {
  window: "24h" | "7d";
}; result: Stats };
  "stats.outlook": { params: {
  /** Headroom: that long back and as long ahead. Cost per day: the last that many days (under 1 day counts as 1). */
  range: "12h" | "24h" | "3d" | "7d" | "14d" | "30d";
  /** Also give `overlay` for this pick: an agent's name, or `grp:<name>` for the group of agents whose dotted names start with `<name>.` (and the agent `<name>` itself), the key of a row of the agents table a client draws. It rides on this call and not on a method of its own because the curve is read against `past` and must be sampled at the same moment as it; one pick costs one array, not one per agent. */
  agent?: string;
}; result: Outlook };
  "settings.get": { params: {
  /** Return that agent's own overrides in `overrides`. */
  agent?: AgentName;
}; result: Settings };
  "settings.set": { params: {
  changes: SettingsChanges;
  /** Write the per-agent override of: park_timeout_s, ignore_cache_expiry, bg_notice_s, bg_grace_s, bg_check_s, notice_digest_s. Any other key with `agent` is bad_request. */
  agent?: AgentName;
  request_id: RequestId;
}; result: Accepted };
  "answers.list": { params: { [key: string]: unknown }; result: {
    answers: StandingAnswer[];
  } };
  "answer.add": { params: {
  /** A folder to trust; must exist. Give folder, or the three rule fields. */
  folder?: string;
  vendor?: string;
  /** The dialog's title; a long command's truncated title is refused. */
  title?: string;
  /** The option label to choose. */
  label?: string;
  request_id: RequestId;
}; result: Accepted };
  "answer.remove": { params: {
  kind: "trust" | "dialog";
  key: string[];
  request_id: RequestId;
}; result: Accepted };
  "devices.list": { params: { [key: string]: unknown }; result: {
    devices: Device[];
  } };
  "device.pair": { params: {
  name?: string;
  /** Seconds the code lives; default 300. */
  ttl_s?: number;
  request_id: RequestId;
}; result: Accepted & {
    code: string;
    expires_at: Timestamp;
    /** Listener certificate fingerprint. */
    fingerprint: string;
    /** What the QR carries. */
    uri: string;
  } };
  "device.redeem": { params: {
  code: string;
  name: string;
  request_id: RequestId;
}; result: Accepted & {
    token: string;
    device: string;
  } };
  "device.revoke": { params: {
  device: string;
  request_id: RequestId;
}; result: Accepted };
}

export type MethodName = keyof Methods;

export const METHODS: { [K in MethodName]: { role: Role; callers: Role[] | null; write: boolean; since: number } } = {
  "hello": { role: "anonymous", callers: null, write: false, since: 1 },
  "whoami": { role: "agent", callers: null, write: false, since: 1 },
  "subscribe": { role: "agent", callers: null, write: false, since: 1 },
  "unsubscribe": { role: "agent", callers: null, write: false, since: 1 },
  "daemon.status": { role: "agent", callers: null, write: false, since: 1 },
  "events.feed": { role: "agent", callers: null, write: false, since: 1 },
  "operation.get": { role: "member", callers: null, write: false, since: 1 },
  "agents.list": { role: "agent", callers: null, write: false, since: 1 },
  "agent.get": { role: "agent", callers: null, write: false, since: 1 },
  "agent.timeline": { role: "member", callers: null, write: false, since: 1 },
  "agent.options": { role: "member", callers: null, write: false, since: 1 },
  "agent.set": { role: "agent", callers: ["agent", "founder"], write: true, since: 1 },
  "agent.fold": { role: "agent", callers: null, write: true, since: 1 },
  "agent.move": { role: "agent", callers: null, write: true, since: 1 },
  "agent.unstall": { role: "founder", callers: null, write: true, since: 1 },
  "agent.create": { role: "founder", callers: null, write: true, since: 1 },
  "session.start": { role: "agent", callers: null, write: true, since: 1 },
  "session.wake": { role: "agent", callers: null, write: true, since: 1 },
  "session.stop": { role: "agent", callers: null, write: true, since: 1 },
  "session.restart": { role: "agent", callers: null, write: true, since: 1 },
  "session.park": { role: "agent", callers: null, write: true, since: 1 },
  "session.input": { role: "member", callers: null, write: true, since: 1 },
  "session.keys": { role: "member", callers: null, write: true, since: 1 },
  "session.type": { role: "member", callers: null, write: true, since: 1 },
  "session.interrupt": { role: "member", callers: null, write: true, since: 1 },
  "prompt.list": { role: "member", callers: null, write: false, since: 1 },
  "prompt.answer": { role: "member", callers: null, write: true, since: 1 },
  "input.take": { role: "member", callers: null, write: true, since: 1 },
  "input.release": { role: "member", callers: null, write: true, since: 1 },
  "chat.messages": { role: "member", callers: null, write: false, since: 1 },
  "chat.requests": { role: "member", callers: null, write: false, since: 1 },
  "chat.transcript": { role: "member", callers: null, write: false, since: 1 },
  "transcript.events": { role: "member", callers: null, write: false, since: 1 },
  "chat.locate": { role: "member", callers: null, write: false, since: 1 },
  "agent.strip": { role: "member", callers: null, write: false, since: 1 },
  "chat.refs": { role: "member", callers: null, write: false, since: 1 },
  "chat.search": { role: "member", callers: null, write: false, since: 1 },
  "chat.send": { role: "member", callers: null, write: true, since: 1 },
  "chat.send_now": { role: "member", callers: null, write: true, since: 1 },
  "chat.dismiss": { role: "member", callers: null, write: true, since: 1 },
  "chat.cancel": { role: "member", callers: null, write: true, since: 1 },
  "chat.seen": { role: "member", callers: null, write: true, since: 1 },
  "attachment.put": { role: "member", callers: null, write: true, since: 1 },
  "attachment.get": { role: "member", callers: null, write: false, since: 1 },
  "terminal.attach": { role: "member", callers: null, write: false, since: 1 },
  "terminal.history": { role: "member", callers: null, write: false, since: 1 },
  "terminal.resize": { role: "member", callers: null, write: true, since: 1 },
  "terminal.input": { role: "member", callers: null, write: true, since: 1 },
  "terminal.close": { role: "member", callers: null, write: true, since: 1 },
  "terminal.open": { role: "member", callers: null, write: false, since: 1 },
  "terminal.detach": { role: "member", callers: null, write: false, since: 1 },
  "inbox.list": { role: "agent", callers: null, write: false, since: 1 },
  "inbox.get": { role: "agent", callers: null, write: false, since: 1 },
  "inbox.mark_read": { role: "agent", callers: null, write: true, since: 1 },
  "inbox.answer": { role: "member", callers: null, write: true, since: 1 },
  "inbox.reply": { role: "agent", callers: null, write: true, since: 1 },
  "tasks.list": { role: "agent", callers: null, write: false, since: 1 },
  "task.get": { role: "agent", callers: null, write: false, since: 1 },
  "task.shot": { role: "member", callers: null, write: false, since: 1 },
  "task.new": { role: "agent", callers: null, write: true, since: 1 },
  "task.handoff": { role: "agent", callers: null, write: true, since: 1 },
  "task.done": { role: "agent", callers: null, write: true, since: 1 },
  "task.reopen": { role: "agent", callers: null, write: true, since: 1 },
  "task.progress": { role: "agent", callers: ["agent"], write: true, since: 1 },
  "progress.get": { role: "agent", callers: ["agent"], write: false, since: 1 },
  "task.begin": { role: "agent", callers: null, write: true, since: 1 },
  "task.start": { role: "agent", callers: null, write: true, since: 1 },
  "task.memo.list": { role: "agent", callers: null, write: false, since: 1 },
  "task.memo.add": { role: "agent", callers: null, write: true, since: 1 },
  "task.memo.edit": { role: "agent", callers: null, write: true, since: 1 },
  "task.memo.delete": { role: "agent", callers: null, write: true, since: 1 },
  "task.queue": { role: "agent", callers: null, write: true, since: 1 },
  "task.draft": { role: "agent", callers: null, write: true, since: 1 },
  "task.merge": { role: "agent", callers: null, write: true, since: 1 },
  "task.release": { role: "agent", callers: null, write: true, since: 1 },
  "task.discard": { role: "agent", callers: null, write: true, since: 1 },
  "task.board": { role: "agent", callers: null, write: false, since: 1 },
  "task.withdraw": { role: "agent", callers: null, write: true, since: 1 },
  "task.cancel": { role: "member", callers: null, write: true, since: 1 },
  "task.edit": { role: "agent", callers: null, write: true, since: 1 },
  "task.ask": { role: "member", callers: null, write: true, since: 1 },
  "say": { role: "agent", callers: null, write: true, since: 1 },
  "land": { role: "agent", callers: ["agent"], write: true, since: 1 },
  "land.pass": { role: "agent", callers: ["agent"], write: true, since: 1 },
  "land.authorize": { role: "agent", callers: ["agent"], write: true, since: 1 },
  "hook.record": { role: "agent", callers: ["agent"], write: true, since: 1 },
  "hr.propose": { role: "agent", callers: null, write: true, since: 1 },
  "hr.withdraw": { role: "agent", callers: null, write: true, since: 1 },
  "trust.propose": { role: "agent", callers: null, write: true, since: 1 },
  "trust.list": { role: "agent", callers: null, write: false, since: 1 },
  "trust.remove": { role: "founder", callers: null, write: true, since: 1 },
  "agent.triggers": { role: "agent", callers: null, write: false, since: 1 },
  "memory.status": { role: "agent", callers: null, write: false, since: 1 },
  "team.get": { role: "agent", callers: null, write: false, since: 1 },
  "hr.list": { role: "agent", callers: null, write: false, since: 1 },
  "hr.decide": { role: "member", callers: null, write: true, since: 1 },
  "accounts.list": { role: "agent", callers: null, write: false, since: 1 },
  "status.pick": { role: "agent", callers: null, write: false, since: 1 },
  "account.set": { role: "founder", callers: null, write: true, since: 1 },
  "account.add": { role: "founder", callers: null, write: true, since: 1 },
  "account.login": { role: "founder", callers: null, write: true, since: 1 },
  "account.login_cancel": { role: "founder", callers: null, write: true, since: 1 },
  "account.login_code": { role: "founder", callers: null, write: true, since: 1 },
  "account.remove": { role: "founder", callers: null, write: true, since: 1 },
  "repos.list": { role: "member", callers: null, write: false, since: 1 },
  "worktrees.list": { role: "member", callers: null, write: false, since: 1 },
  "worktree.diff": { role: "member", callers: null, write: false, since: 1 },
  "worktree.set_status": { role: "member", callers: null, write: true, since: 1 },
  "schedules.list": { role: "agent", callers: null, write: false, since: 1 },
  "schedule.add": { role: "founder", callers: null, write: true, since: 1 },
  "schedule.edit": { role: "founder", callers: null, write: true, since: 1 },
  "schedule.remove": { role: "founder", callers: null, write: true, since: 1 },
  "stats.get": { role: "member", callers: null, write: false, since: 1 },
  "stats.outlook": { role: "member", callers: null, write: false, since: 1 },
  "settings.get": { role: "founder", callers: null, write: false, since: 1 },
  "settings.set": { role: "founder", callers: null, write: true, since: 1 },
  "answers.list": { role: "founder", callers: null, write: false, since: 1 },
  "answer.add": { role: "founder", callers: null, write: true, since: 1 },
  "answer.remove": { role: "founder", callers: null, write: true, since: 1 },
  "devices.list": { role: "founder", callers: null, write: false, since: 1 },
  "device.pair": { role: "founder", callers: null, write: true, since: 1 },
  "device.redeem": { role: "anonymous", callers: null, write: true, since: 1 },
  "device.revoke": { role: "founder", callers: null, write: true, since: 1 },
};

export const PROTOCOL = 1;

export type ErrorCode = "not_found" | "refused" | "stale_prompt" | "daemon_busy" | "bad_request" | "internal" | "daemon_down";
export const RPC_CODES: { [K in ErrorCode]: number | null } = {
  not_found: -32001,
  refused: -32002,
  stale_prompt: -32003,
  daemon_busy: -32004,
  bad_request: -32600,
  internal: -32603,
  daemon_down: null,
};

export interface Notifications {
  event: Event;
  gap: {
  reason: "retention" | "slow_client";
  cursor: number;
  topic?: Topic;
};
  frame: {
  terminal: string;
  seq: number;
  full: boolean;
  lines?: Row[];
  changed?: {
    y: number;
    row: Row;
  }[];
  cursor: Cursor;
  /** Present on a `styled` attach's frames and absent on a text one's, so a connection that watches one terminal in both formats can tell which attach a frame belongs to. */
  format?: "styled";
};
  resize: {
  terminal: string;
  seq: number;
  cols: number;
  rows: number;
};
  marker: {
  terminal: string;
  seq: number;
  at: Timestamp;
  kind: "daemon_typed" | "control";
  actor: Actor | null;
  text?: string;
  /** The attach this notice belongs to, whose counter `seq` is: a terminal watched in both formats is sent each of these twice. Additive: a client that does not know it ignores it. */
  format: "text" | "bytes" | "styled";
};
  holder: {
  terminal: string;
  seq: number;
  holders: InputHolder[];
  /** The attach this notice belongs to, whose counter `seq` is: a terminal watched in both formats is sent each of these twice. Additive: a client that does not know it ignores it. */
  format: "text" | "bytes" | "styled";
};
  output: {
  terminal: string;
  seq: number;
  /** Base64 of what the program wrote. */
  data: string;
};
  snapshot: {
  terminal: string;
  seq: number;
  reason: "resize" | "slow_client";
  cols: number;
  rows: number;
  /** Base64, as in the attach snapshot. */
  data: string;
  alt_screen: boolean;
  cursor: {
    x: number;
    y: number;
  };
  cursor_visible: boolean;
};
}

/** The state words the daemon sends; an open set, so a client keeps a fallback. */
export const STATE_WORDS = ["running", "idle", "queued", "parked", "waiting", "stalled", "blocked", "no_terminal"] as const;

export const ROLE_ORDER: Role[] = ["anonymous", "agent", "member", "founder"];
