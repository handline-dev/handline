// Generated from contract/spec/ by contract/generate.py; do not edit.
/* eslint-disable */
import type { Accepted, Actor, AgentName, InputHolder, Prompt, ReadCursor, Repo, RequestId, RequestName, Role, Status, Timestamp } from './core.ts';

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
  /** The account the agent is on: the one its session runs on, or, at rest, the one whose prompt cache still holds its conversation, until cache_until. null at rest once cache_until has passed or is null: the agent is on no account and counts on none. Its next wake goes to the account with the most weekly headroom (furthest behind its pace line, Account.pace; with pacing off, the most left of its weekly window), carrying the conversation there; it starts fresh from its handoff only when the conversation cannot be carried. While the cache lives a wake stays on its account, unless that account cannot run it now (its wall, its pace, no free slot), when the usual moves apply. */
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
  /** What a running agent is doing now, for the line under its name: while state is running and handoff is null, a client draws it in place of status.text. null when no turn runs or the turn has called no tool yet. Absent from a daemon that does not send it. A change of doing alone is pushed (agent.changed) at most once every 5 s, with the newest value. */
  doing?: {
    /** From the running turn's newest tool item of chat.transcript: its `tool`, a space and the first line of its `text`, or the tool alone when that text is empty; cut to 60 characters (Unicode code points, no ellipsis). */
    text: string;
    /** When that call began. */
    at: Timestamp;
  } | null;
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
  /** Set while the agent waits on a decision only a person at its terminal can make. Fed by hooks (PermissionRequest and the permission and question Notification kinds) and by the session file saying status `waiting` with `waitingFor` (`permission prompt` is the one measured; the file has no needs or tempo key), never by reading the screen; the tool and its target come from the PermissionRequest hook event; cleared by the hook that follows (PostToolUse, UserPromptSubmit, Stop, SessionEnd) or when the session file stops saying it. While set, `state` is `waiting`, `has_dialog` is true and `attention` is `alert`. null when none. */
  needs_you?: NeedsYou | null;
  /** Set while a session hands off, so a chat that sits still can say why. Cleared when the new session's start item appears. Every change is an agent.changed. A handoff that waits for a usage window to reset shows `writing` only; it is not `starting` until a launch is due. null when none. Absent from a daemon that does not serve it. */
  handoff?: {
    /** Why the conversation is ending: the words of session.cause (`context_full`, `move`, and the trigger's own word for the rest, such as `usage` or `login`). An open set. */
    cause: string;
    /** `writing`: the agent writes its handoff note and the daemon takes the record. `starting`: the old session is closed and the new one is launching. */
    phase: "writing" | "starting";
    since: Timestamp;
  } | null;
  /** The agent's newest request to the founder; null when it never wrote to them. Absent from a daemon that does not serve it yet. */
  last_to_founder?: {
    /** The whole body of its newest request to the founder (a say, ask or decision), whether or not it was read. */
    text: string;
    at: Timestamp;
  } | null;
  /** How many of the agent's open requests to the founder they have not seen yet (past the for_you_seen cursor, as `hl inbox` counts them; founder_seen is that cursor), less the asks its task comments raised, which have cards of their own. Absent from a daemon that does not serve it yet. */
  founder_unread?: number;
  /** The highest message number of this agent the founder has had in view (the for_you_seen cursor chat.seen moves); 0 when none. A chat opens at the first of the agent's messages above it, under an unread divider. Absent from a daemon that does not serve it yet: no divider. */
  founder_seen?: number;
  /** What it is doing and why (x-status). Absent from a daemon that does not send it. */
  status?: Status;
};

export type AgentDetail = AgentView & {
  worktree: {
    path: string;
    branch: string;
    /** owner/repo of the worktree's origin when it is on GitHub (https or ssh), else null. */
    repo: string | null;
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

/** The result of a write that starts a long-running operation. */
export type OperationStarted = Accepted & {
  /** The id of the operation; operation.get and the operations topic follow it. */
  operation: string;
};

/** An agent's settings, every value a string as `hl agent set <agent> key=value` takes it. Keys an agent may change on itself: model, effort, context_k, handoff_note. Every other key is the founder's (an agent proposes it with hr.propose kind settings). */
export type AgentSettingValues = {
  model?: string;
  effort?: "low" | "medium" | "high" | "xhigh" | "max";
  weight?: "light" | "heavy";
  repo?: string;
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
  /** Permission mode. */
  mode?: "default" | "auto" | "acceptEdits" | "plan";
};

export type NeedsYou = {
  /** permission: a tool or a folder waits for a yes. question: the agent asked the person something. usage: the session limit's choice. dialog: any other (a start-up dialog the daemon will not answer included), and any the daemon does not know: unknown is a person. stuck: the daemon retried, restarted and relaunched the agent and it is still stuck: one card, gone when the trouble is. The same event is never two cards, and a load hold is never stuck. */
  kind: "permission" | "question" | "usage" | "dialog" | "stuck";
  since: Timestamp;
  /** One line, from the hook or the session file; never read from the screen. */
  text: string | null;
  /** hook: PermissionRequest or Notification. session: the session's own file (sessions/<pid>.json status waiting and waitingFor). daemon: the daemon's own record (a start-up dialog stall, an agent it could not heal); never a task. */
  source: "hook" | "session" | "daemon";
};

export interface AgentsMethods {
  "agents.list": { params: {
  /** Add the status-line fields of each agent (started, has_repo, has_terminal, screen_word, screen_detail, unreached, task_waits, wake_wait_reason), read at the call; screen_word and screen_detail may be as old as the stored view. Default false. */
  status?: boolean;
}; result: {
    agents: AgentView[];
  } & ReadCursor };
  "agent.get": { params: {
  agent: AgentName;
}; result: AgentDetail & ReadCursor };
  "pr.exists": { params: {
  agent: AgentName;
  numbers: number[];
}; result: {
    repo: string | null;
    found: number[];
  } };
  "agent.timeline": { params: {
  agent: AgentName;
  /** Return rows older than this id. */
  before?: number;
  /** Default 50, at most 200. */
  limit?: number;
}; result: {
    items: TimelineEntry[];
    more: boolean;
  } & ReadCursor };
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
  "agent.force_start": { params: {
  agent: AgentName;
  request_id: RequestId;
}; result: Accepted };
  "agent.retire": { params: {
  agent: AgentName;
  /** The founder's answer for a worktree of the agent that holds work: save (what is left is committed to a new branch rescue/<agent>-<date>, then it is removed) or force (removed with whatever it holds; its branch stays; it also removes one that is on no branch or on main). keep keeps the worktree, so the agent is not retired (gate worktree). Another word is bad_request, field worktree. */
  worktree?: "keep" | "save" | "force";
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
  } & ReadCursor };
  "prompt.answer": { params: {
  agent: AgentName;
  prompt: string;
  /** A PromptOption.key, or {text} for the free-text answer to a dialog that asks for one; a text answer passes the same gates and the prompt check. An array only answers a `multi` prompt. */
  choice: string | string[] | {
    text: string;
  };
  /** With choice deny on a prompt whose source is hook: a short reason, given to the agent with the deny. bad_request (field note) on any other answer. */
  note?: string;
  request_id: RequestId;
}; result: Accepted };
  "permission.ask": { params: {
  /** The tool's name, as the PermissionRequest hook gives it. */
  tool: string;
  /** The tool call's input as the hook gives it (tool_input): for Bash, command and description. */
  input: { [key: string]: unknown };
}; result: {
    /** The founder's answer; null when the call ended with none (an hour passed), and the agent's own dialog shows in its terminal. */
    behavior: ("allow" | "deny") | null;
    /** With deny: what the agent is told, with the founder's note when one was given. */
    message?: string;
  } };
  "input.take": { params: {
  agent: AgentName;
  /** Take the input from the person or device that holds it instead of being refused; the holder notice tells them. It never types into the agent's own turn: typing still waits for it. */
  force?: boolean;
  request_id: RequestId;
}; result: Accepted & {
    holder: InputHolder;
  } };
  "input.release": { params: {
  agent: AgentName;
  request_id: RequestId;
}; result: Accepted };
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
  } & ReadCursor };
  "progress.get": { params: { [key: string]: unknown }; result: {
    /** The note, or null when none is kept. */
    note: string | null;
  } };
  "say": { params: {
  target: string;
  /** Markdown. It may name images (x-attachments, texts). */
  body: string;
  task?: number;
  /** Why this carries no task. */
  no_task?: string;
  parent?: RequestName;
  /** Absolute paths of images the message carries (ChatMessage.attachments): PNG, JPEG, GIF or WebP, at most 10 MiB each, at most 6. The daemon reads each and keeps a copy under its home. An agent may name only files under its worktree, its folder or the scratch folder; the founder any. A path outside those, a file that is not an image, or an image whose first bytes do not match its ending is bad_request (field files); the message is not written. */
  files?: string[];
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
}

export const AGENTS_METHODS: { [K in keyof AgentsMethods]: { role: Role; callers: Role[] | null; write: boolean; since: number } } = {
  "agents.list": { role: "agent", callers: null, write: false, since: 1 },
  "agent.get": { role: "agent", callers: null, write: false, since: 1 },
  "pr.exists": { role: "member", callers: null, write: false, since: 1 },
  "agent.timeline": { role: "member", callers: null, write: false, since: 1 },
  "agent.options": { role: "member", callers: null, write: false, since: 1 },
  "agent.set": { role: "agent", callers: ["agent", "founder"], write: true, since: 1 },
  "agent.fold": { role: "agent", callers: null, write: true, since: 1 },
  "agent.move": { role: "agent", callers: null, write: true, since: 1 },
  "agent.unstall": { role: "founder", callers: null, write: true, since: 1 },
  "agent.force_start": { role: "founder", callers: null, write: true, since: 1 },
  "agent.retire": { role: "founder", callers: null, write: true, since: 1 },
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
  "permission.ask": { role: "agent", callers: null, write: false, since: 1 },
  "input.take": { role: "member", callers: null, write: true, since: 1 },
  "input.release": { role: "member", callers: null, write: true, since: 1 },
  "agent.strip": { role: "member", callers: null, write: false, since: 1 },
  "progress.get": { role: "agent", callers: ["agent"], write: false, since: 1 },
  "say": { role: "agent", callers: null, write: true, since: 1 },
  "agent.triggers": { role: "agent", callers: null, write: false, since: 1 },
};
