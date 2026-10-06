// Generated from contract/protocol.json by contract/generate.py; do not edit.
/* eslint-disable */

/** UTC, RFC 3339. */
export type Timestamp = string;

/** An agent's name, e.g. orcapipe.daemon.contract. */
export type AgentName = string;

/** Ordered lowest to highest. A method's role is the lowest that may call it; every higher role may too. */
export type Role = "anonymous" | "agent" | "member" | "founder";

export type Topic = "status" | "accounts" | "agents" | "inbox" | "tasks" | "notifications" | "operations";

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
  /** Opaque; changes whenever the open prompt changes. */
  id: string;
  agent: AgentName;
  /** e.g. permission, trust, dialog; open set. */
  kind: string;
  tool?: string | null;
  command?: string | null;
  reason?: string | null;
  lines: string[];
  options: PromptOption[];
  since: Timestamp;
};

export type Block = {
  kind: string;
  since: Timestamp;
};

/** The stored per-agent view; the daemon decided every field. */
export type AgentView = {
  name: AgentName;
  role: string;
  model: string | null;
  effort: string | null;
  /** The daemon's state word; open set, clients ignore unknown. */
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
  has_dialog: boolean;
  parent: AgentName | null;
};

export type AgentDetail = AgentView & {
  worktree: {
    path: string;
    branch: string;
  } | null;
  terminal: string | null;
  session_id: string | null;
  session_open: boolean;
  input_holder: InputHolder;
  prompt: Prompt | null;
};

export type TimelineEntry = {
  id: number;
  at: Timestamp;
  /** state, task_delivered, handoff, key, input, interrupt, start, stop, ...; open set. */
  kind: string;
  actor: Actor | null;
  text: string;
  data?: { [key: string]: unknown };
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
};

export type TaskDetail = Task & {
  body: string;
  deliveries: {
    at: Timestamp;
    how: string;
    outcome: string;
  }[];
  events: Event[];
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
};

export type Event = {
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
  cap_pct: number | null;
  agents: AgentName[];
  moves: {
    at: Timestamp;
    agent: AgentName;
    from: string | null;
    to: string;
    reason: string;
  }[];
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
  agent: AgentName;
  title: string;
  /** Unified diff. */
  diff: string;
  state: "open" | "approved" | "rejected";
  created: Timestamp;
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
};

export type Device = {
  id: string;
  name: string;
  role: "member";
  created: Timestamp;
  last_seen: Timestamp | null;
  connected: boolean;
};

/** One terminal row: plain text, no escapes. */
export type Row = {
  text: string;
  /** Reserved for styled rows. The daemon never sets it while frames are text-only; clients ignore it. */
  spans?: {
    from: number;
    to: number;
    style: { [key: string]: unknown };
  }[];
};

export type Cursor = {
  x: number;
  y: number;
} | null;

export type TerminalSnapshot = {
  terminal: string;
  /** `styled` is reserved. */
  format: "text";
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
  holder: InputHolder;
};

export type HistoryPage = {
  first: number;
  lines: Row[];
  more: boolean;
};

/** Chosen by the caller; unique per intended action. */
export type RequestId = string;

export interface Methods {
  "hello": { params: {
  /** name/version of the caller. */
  client: string;
  /** The protocol the client will speak. */
  protocol: number;
  /** Device token; network listener only. */
  token?: string;
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
    methods: {
      name: string;
      since: number;
      role: Role;
      allowed: boolean;
    }[];
  } };
  "whoami": { params: { [key: string]: unknown }; result: {
    caller: Caller;
    role: Role;
    methods: string[];
  } };
  "subscribe": { params: {
  topics: Topic[];
  /** Last cursor seen. Absent: live only. */
  since?: number;
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
  "agents.list": { params: { [key: string]: unknown }; result: {
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
  "agent.create": { params: {
  name: AgentName;
  role: string;
  repo: string;
  base?: string;
  model?: string;
  effort?: string;
  account?: string;
  parent?: AgentName;
  request_id: RequestId;
}; result: Accepted & {
    /** Operation id. */
    operation: string;
  } };
  "agent.set": { params: {
  agent: AgentName;
  values: { [key: string]: unknown };
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
  "session.start": { params: {
  agent: AgentName;
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
  /** A PromptOption.key. */
  choice: string;
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
  "terminal.attach": { params: {
  agent?: AgentName;
  /** Handle; give agent or terminal, not both. */
  terminal?: string;
  /** History lines to include; default 100. */
  history?: number;
}; result: TerminalSnapshot };
  "terminal.history": { params: {
  terminal: string;
  before: number;
  limit: number;
}; result: HistoryPage };
  "terminal.resize": { params: {
  terminal: string;
  cols: number;
  rows: number;
  request_id: RequestId;
}; result: Accepted };
  "terminal.detach": { params: {
  terminal: string;
}; result: { [key: string]: unknown } };
  "inbox.list": { params: {
  unread?: boolean;
  kind?: string;
  agent?: AgentName;
  /** Return rows older than this id. */
  before?: number;
  /** Default 50, at most 200. */
  limit?: number;
}; result: {
    items: InboxItem[];
    more: boolean;
  } };
  "inbox.get": { params: {
  id: number;
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
}; result: {
    items: Task[];
    more: boolean;
  } };
  "task.get": { params: {
  task: number;
}; result: TaskDetail };
  "task.new": { params: {
  owner: string;
  title: string;
  body: string;
  time_box: Duration;
  relation?: string;
  response?: string;
  parent?: number;
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
  evidence: string;
  request_id: RequestId;
}; result: Accepted };
  "task.reopen": { params: {
  task: number;
  note?: string;
  request_id: RequestId;
}; result: Accepted };
  "task.progress": { params: {
  task: number;
  note: string;
  request_id: RequestId;
}; result: Accepted };
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
  title?: string;
  body?: string;
  time_box?: Duration;
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
  "ask": { params: {
  target: string;
  title: string;
  body: string;
  options?: DecisionOption[];
  request_id: RequestId;
}; result: Accepted & {
    request: number;
  } };
  "push": { params: {
  target: AgentName;
  request?: number;
  request_id: RequestId;
}; result: Accepted };
  "push.answer": { params: {
  requester: AgentName;
  request_id: RequestId;
}; result: Accepted };
  "push.cancelled": { params: {
  request: number;
  request_id: RequestId;
}; result: Accepted };
  "land": { params: {
  sha: string;
  message: string;
  tree?: string;
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
  "team.get": { params: { [key: string]: unknown }; result: {
    agents: TeamAgent[];
    edges: {
      from: string;
      to: string;
    }[];
  } };
  "hr.list": { params: {
  state?: string;
}; result: {
    proposals: Proposal[];
  } };
  "hr.decide": { params: {
  proposal: number;
  decision: "approve" | "reject";
  note?: string;
  request_id: RequestId;
}; result: Accepted };
  "accounts.list": { params: { [key: string]: unknown }; result: {
    accounts: Account[];
  } };
  "account.set_cap": { params: {
  account: string;
  cap_pct: number | null;
  request_id: RequestId;
}; result: Accepted };
  "account.login": { params: {
  account: string;
  request_id: RequestId;
}; result: Accepted & {
    terminal: string;
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
  "settings.get": { params: { [key: string]: unknown }; result: Settings };
  "settings.set": { params: {
  changes: { [key: string]: unknown };
  request_id: RequestId;
}; result: Accepted };
  "settings.trust": { params: {
  account: string;
  worktrees: {
    agent: AgentName;
    path: string;
  }[];
  request_id: RequestId;
}; result: Accepted };
  "devices.list": { params: { [key: string]: unknown }; result: {
    devices: Device[];
  } };
  "device.pair": { params: {
  name?: string;
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

export const METHODS: { [K in MethodName]: { role: Role; write: boolean; since: number } } = {
  "hello": { role: "anonymous", write: false, since: 1 },
  "whoami": { role: "agent", write: false, since: 1 },
  "subscribe": { role: "agent", write: false, since: 1 },
  "unsubscribe": { role: "agent", write: false, since: 1 },
  "daemon.status": { role: "member", write: false, since: 1 },
  "events.feed": { role: "member", write: false, since: 1 },
  "operation.get": { role: "member", write: false, since: 1 },
  "agents.list": { role: "agent", write: false, since: 1 },
  "agent.get": { role: "agent", write: false, since: 1 },
  "agent.timeline": { role: "member", write: false, since: 1 },
  "agent.options": { role: "member", write: false, since: 1 },
  "agent.create": { role: "founder", write: true, since: 1 },
  "agent.set": { role: "agent", write: true, since: 1 },
  "agent.fold": { role: "member", write: true, since: 1 },
  "agent.move": { role: "member", write: true, since: 1 },
  "session.start": { role: "agent", write: true, since: 1 },
  "session.wake": { role: "agent", write: true, since: 1 },
  "session.stop": { role: "agent", write: true, since: 1 },
  "session.restart": { role: "agent", write: true, since: 1 },
  "session.park": { role: "agent", write: true, since: 1 },
  "session.input": { role: "member", write: true, since: 1 },
  "session.keys": { role: "member", write: true, since: 1 },
  "session.interrupt": { role: "member", write: true, since: 1 },
  "prompt.list": { role: "member", write: false, since: 1 },
  "prompt.answer": { role: "member", write: true, since: 1 },
  "input.take": { role: "member", write: true, since: 1 },
  "input.release": { role: "member", write: true, since: 1 },
  "terminal.attach": { role: "member", write: false, since: 1 },
  "terminal.history": { role: "member", write: false, since: 1 },
  "terminal.resize": { role: "member", write: true, since: 1 },
  "terminal.detach": { role: "member", write: false, since: 1 },
  "inbox.list": { role: "agent", write: false, since: 1 },
  "inbox.get": { role: "agent", write: false, since: 1 },
  "inbox.mark_read": { role: "agent", write: true, since: 1 },
  "inbox.answer": { role: "member", write: true, since: 1 },
  "inbox.reply": { role: "agent", write: true, since: 1 },
  "tasks.list": { role: "agent", write: false, since: 1 },
  "task.get": { role: "agent", write: false, since: 1 },
  "task.new": { role: "agent", write: true, since: 1 },
  "task.handoff": { role: "agent", write: true, since: 1 },
  "task.done": { role: "agent", write: true, since: 1 },
  "task.reopen": { role: "agent", write: true, since: 1 },
  "task.progress": { role: "agent", write: true, since: 1 },
  "task.withdraw": { role: "member", write: true, since: 1 },
  "task.cancel": { role: "member", write: true, since: 1 },
  "task.edit": { role: "member", write: true, since: 1 },
  "say": { role: "agent", write: true, since: 1 },
  "ask": { role: "agent", write: true, since: 1 },
  "push": { role: "agent", write: true, since: 1 },
  "push.answer": { role: "agent", write: true, since: 1 },
  "push.cancelled": { role: "agent", write: true, since: 1 },
  "land": { role: "agent", write: true, since: 1 },
  "land.pass": { role: "agent", write: true, since: 1 },
  "land.authorize": { role: "agent", write: true, since: 1 },
  "hook.record": { role: "agent", write: true, since: 1 },
  "team.get": { role: "agent", write: false, since: 1 },
  "hr.list": { role: "member", write: false, since: 1 },
  "hr.decide": { role: "founder", write: true, since: 1 },
  "accounts.list": { role: "member", write: false, since: 1 },
  "account.set_cap": { role: "founder", write: true, since: 1 },
  "account.login": { role: "founder", write: true, since: 1 },
  "repos.list": { role: "member", write: false, since: 1 },
  "worktrees.list": { role: "member", write: false, since: 1 },
  "worktree.diff": { role: "member", write: false, since: 1 },
  "worktree.set_status": { role: "member", write: true, since: 1 },
  "schedules.list": { role: "member", write: false, since: 1 },
  "schedule.add": { role: "founder", write: true, since: 1 },
  "schedule.edit": { role: "founder", write: true, since: 1 },
  "schedule.remove": { role: "founder", write: true, since: 1 },
  "stats.get": { role: "member", write: false, since: 1 },
  "settings.get": { role: "founder", write: false, since: 1 },
  "settings.set": { role: "founder", write: true, since: 1 },
  "settings.trust": { role: "founder", write: true, since: 1 },
  "devices.list": { role: "founder", write: false, since: 1 },
  "device.pair": { role: "founder", write: true, since: 1 },
  "device.redeem": { role: "anonymous", write: true, since: 1 },
  "device.revoke": { role: "founder", write: true, since: 1 },
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
};
}

export const ROLE_ORDER: Role[] = ["anonymous", "agent", "member", "founder"];
