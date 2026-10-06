// Generated from contract/protocol.json by contract/generate.py; do not edit.
/* eslint-disable */

/** UTC, RFC 3339. */
export type Timestamp = string;

/** An agent's name, as hl team lists it. */
export type AgentName = string;

/** Ordered lowest to highest. A method's role is the lowest that may call it; every higher role may too. */
export type Role = "anonymous" | "agent" | "member" | "founder";

/** chat is added for the chat view (the UI design update): messages, landing state, transcript items and working state of one agent; events carry `agent`. */
export type Topic = "status" | "accounts" | "agents" | "inbox" | "tasks" | "notifications" | "operations" | "chat";

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
  /** Opaque; changes whenever the open prompt changes (the daemon uses the screen block's fingerprint). */
  id: string;
  agent: AgentName;
  /** e.g. permission, trust, dialog; open set. */
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
  /** Who holds the input; one in v1, a list so more can later. */
  input_holders?: InputHolder[];
  /** The text drawn beside the state word (a clock, what a wait is for), at most 40 characters; empty when there is none. The state column is drawn from state and state_detail alone. */
  state_detail: string;
};

export type AgentDetail = AgentView & {
  worktree: {
    path: string;
    branch: string;
  } | null;
  terminal: string | null;
  session_id: string | null;
  session_open: boolean;
  /** The account the daemon would place a session start on, before session.start; account null with a reason when none can take work. */
  account_pick: {
    account: string | null;
    reason: string;
  };
  /** The agent's open prompt, or null. */
  prompt: Prompt | null;
  settings?: AgentSettingValues;
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
  events: TaskEvent[];
  memos: Memo[];
  blockers: {
    task: number;
    reason: string;
  }[];
  children: number[];
  handoffs: {
    at: Timestamp;
    from: string;
    to: string;
    note: string;
  }[];
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
  /** Digest of what the proposal changes as drawn; hr.decide's approve hands it back. */
  subject: string;
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
};

export type TaskBoardRow = {
  task: Task;
  holder: AgentName | null;
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
};

export type TranscriptItem = {
  /** Ordered and stable, so a client appends and never redraws. */
  id: number;
  at: Timestamp;
  kind: "reply" | "tool" | "prompt" | "task_event";
  text?: string;
  tool?: string;
  /** Prompt card; `open` says whether it can still be answered. */
  prompt?: Prompt;
  open?: boolean;
  task_event?: {
    event: "opened" | "changed" | "done" | "withdrawn";
    task: number;
    request: number | null;
    evidence: string | null;
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
  /** input.ask is implemented; reserved. */
  input_ask: boolean;
  terminal_format: ("text" | "styled")[];
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
  /** Answer dialogs by the standing rules by itself. */
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
  /** Answer dialogs by the standing rules by itself. null removes the key (back to the default). */
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
  /** A PromptOption.key, or {text} for the free-text answer to a dialog that asks for one; a text answer passes the same gates and the prompt check. */
  choice: string | {
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
  "input.ask": { params: {
  agent: AgentName;
  request_id: RequestId;
}; result: Accepted };
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
  request_id: RequestId;
}; result: Accepted & {
    request: number;
  } };
  "chat.answer": { params: {
  request: number;
  text: string;
  request_id: RequestId;
}; result: Accepted };
  "chat.dismiss": { params: {
  request: number;
  request_id: RequestId;
}; result: Accepted };
  "chat.withdraw": { params: {
  request: number;
  /** Why; stored as typed. Empty is bad_request. */
  reason?: string;
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
  "terminal.close": { params: {
  agent: AgentName;
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
  /** Hold the call up to this many seconds while no item matches the other params (unread, kind, agent, before; read items count unless unread is true), and return when one arrives (an empty list on timeout). Default 0, at most 600; the call is in flight and the connection is not idle. */
  wait_s?: number;
}; result: {
    items: InboxItem[];
    more: boolean;
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
  /** Cannot be worked until this task closes. */
  blocked_by?: number;
  /** 1 high, 2 default, 3 low (written P1-P3). */
  priority?: 1 | 2 | 3;
  expects?: Expects;
  /** Also answer the founder's message with text, where {task} is this task's real number. */
  reply?: {
    message: number;
    /** {task} is replaced by the new task's number. */
    text: string;
  };
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
  "task.board": { params: { [key: string]: unknown }; result: {
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
  title?: string;
  body?: string;
  time_box?: Duration;
  /** Owner only: what the task waits on. */
  waits_on?: string;
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
  why: string;
  request_id: RequestId;
}; result: Accepted & {
    proposal: string;
  } };
  "trust.list": { params: {
  proposals?: boolean;
}; result: {
    roots: string[];
    proposals: {
      id: string;
      root: string;
      state: string;
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
  "accounts.list": { params: { [key: string]: unknown }; result: {
    accounts: Account[];
  } };
  "account.set_cap": { params: {
  account: string;
  /** Most agents mid-turn at once (a count, not a percentage). */
  cap: number;
  request_id: RequestId;
}; result: Accepted };
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
  "settings.trust": { params: {
  account: string;
  worktrees: {
    agent: AgentName;
    path: string;
  }[];
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
  "agent.create": { role: "founder", callers: null, write: true, since: 1 },
  "agent.set": { role: "agent", callers: ["agent", "founder"], write: true, since: 1 },
  "agent.fold": { role: "agent", callers: null, write: true, since: 1 },
  "agent.move": { role: "agent", callers: null, write: true, since: 1 },
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
  "input.ask": { role: "member", callers: null, write: true, since: 1 },
  "input.release": { role: "member", callers: null, write: true, since: 1 },
  "chat.messages": { role: "member", callers: null, write: false, since: 1 },
  "chat.transcript": { role: "member", callers: null, write: false, since: 1 },
  "chat.locate": { role: "member", callers: null, write: false, since: 1 },
  "chat.search": { role: "member", callers: null, write: false, since: 1 },
  "chat.send": { role: "member", callers: null, write: true, since: 1 },
  "chat.answer": { role: "member", callers: null, write: true, since: 1 },
  "chat.dismiss": { role: "member", callers: null, write: true, since: 1 },
  "chat.withdraw": { role: "agent", callers: null, write: true, since: 1 },
  "terminal.attach": { role: "member", callers: null, write: false, since: 1 },
  "terminal.history": { role: "member", callers: null, write: false, since: 1 },
  "terminal.resize": { role: "member", callers: null, write: true, since: 1 },
  "terminal.close": { role: "member", callers: null, write: true, since: 1 },
  "terminal.detach": { role: "member", callers: null, write: false, since: 1 },
  "inbox.list": { role: "agent", callers: null, write: false, since: 1 },
  "inbox.get": { role: "agent", callers: null, write: false, since: 1 },
  "inbox.mark_read": { role: "agent", callers: null, write: true, since: 1 },
  "inbox.answer": { role: "member", callers: null, write: true, since: 1 },
  "inbox.reply": { role: "agent", callers: null, write: true, since: 1 },
  "tasks.list": { role: "agent", callers: null, write: false, since: 1 },
  "task.get": { role: "agent", callers: null, write: false, since: 1 },
  "task.new": { role: "agent", callers: null, write: true, since: 1 },
  "task.handoff": { role: "agent", callers: null, write: true, since: 1 },
  "task.done": { role: "agent", callers: null, write: true, since: 1 },
  "task.reopen": { role: "agent", callers: null, write: true, since: 1 },
  "task.progress": { role: "agent", callers: ["agent"], write: true, since: 1 },
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
  "say": { role: "agent", callers: null, write: true, since: 1 },
  "ask": { role: "agent", callers: null, write: true, since: 1 },
  "push": { role: "agent", callers: null, write: true, since: 1 },
  "push.answer": { role: "agent", callers: null, write: true, since: 1 },
  "push.cancelled": { role: "agent", callers: null, write: true, since: 1 },
  "land": { role: "agent", callers: ["agent"], write: true, since: 1 },
  "land.pass": { role: "agent", callers: ["agent"], write: true, since: 1 },
  "land.authorize": { role: "agent", callers: ["agent"], write: true, since: 1 },
  "hook.record": { role: "agent", callers: ["agent"], write: true, since: 1 },
  "hr.propose": { role: "agent", callers: null, write: true, since: 1 },
  "hr.withdraw": { role: "agent", callers: null, write: true, since: 1 },
  "trust.propose": { role: "agent", callers: null, write: true, since: 1 },
  "trust.list": { role: "agent", callers: null, write: false, since: 1 },
  "trust.remove": { role: "agent", callers: null, write: true, since: 1 },
  "agent.triggers": { role: "agent", callers: null, write: false, since: 1 },
  "memory.status": { role: "agent", callers: null, write: false, since: 1 },
  "team.get": { role: "agent", callers: null, write: false, since: 1 },
  "hr.list": { role: "agent", callers: null, write: false, since: 1 },
  "hr.decide": { role: "founder", callers: null, write: true, since: 1 },
  "accounts.list": { role: "agent", callers: null, write: false, since: 1 },
  "account.set_cap": { role: "founder", callers: null, write: true, since: 1 },
  "account.set": { role: "founder", callers: null, write: true, since: 1 },
  "account.login": { role: "founder", callers: null, write: true, since: 1 },
  "repos.list": { role: "member", callers: null, write: false, since: 1 },
  "worktrees.list": { role: "member", callers: null, write: false, since: 1 },
  "worktree.diff": { role: "member", callers: null, write: false, since: 1 },
  "worktree.set_status": { role: "member", callers: null, write: true, since: 1 },
  "schedules.list": { role: "agent", callers: null, write: false, since: 1 },
  "schedule.add": { role: "founder", callers: null, write: true, since: 1 },
  "schedule.edit": { role: "founder", callers: null, write: true, since: 1 },
  "schedule.remove": { role: "founder", callers: null, write: true, since: 1 },
  "stats.get": { role: "member", callers: null, write: false, since: 1 },
  "settings.get": { role: "founder", callers: null, write: false, since: 1 },
  "settings.set": { role: "founder", callers: null, write: true, since: 1 },
  "settings.trust": { role: "founder", callers: null, write: true, since: 1 },
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
  holder: {
  terminal: string;
  seq: number;
  holders: InputHolder[];
};
}

export const ROLE_ORDER: Role[] = ["anonymous", "agent", "member", "founder"];
