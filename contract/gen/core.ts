// Generated from contract/spec/ by contract/generate.py; do not edit.
/* eslint-disable */
import type { Proposal } from './team.ts';

/** UTC, RFC 3339. */
export type Timestamp = string;

/** An agent's name, as hl team lists it. */
export type AgentName = string;

/** Ordered lowest to highest. A method's role is the lowest that may call it; every higher role may too. */
export type Role = "anonymous" | "agent" | "member" | "founder";

/** chat is added for the chat view (the UI design update): messages, landing state, transcript items and working state of one agent; events carry `agent`. devices (founder, which includes a paired device; subscribed on its own, live only): pairing and revocation of devices. hr: HR proposals, so a client's tab dot is live; any proven caller, as hr.list. transcript: the per-agent event stream of a structured session (transcript.event), served only for an agent the daemon runs as a structured session; see the Transcript stream section. prefs (member, which includes the founder): the person's view preferences, so a pin made on one device shows on the others. requests (member, which includes the founder): the founder's requests (RequestRow), so a Requests page is live. */
export type Topic = "status" | "accounts" | "agents" | "inbox" | "tasks" | "notifications" | "operations" | "chat" | "devices" | "hr" | "transcript" | "prefs" | "requests";

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
  /** One sentence for a person. */
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
  /** With gate duplicate_title: the open task the new one repeats. */
  task?: number;
  /** With gate open_children: the task's open child tasks. */
  children?: {
    task: number;
    state: string;
    assignee: string;
    title: string;
  }[];
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
  /** hook: asked through permission.ask, answered allow or deny with no terminal typed into. Absent for a prompt read from the agent's screen. */
  source?: "hook";
};

/** What an agent or task is doing and why (x-status). */
export type Status = {
  /** One of x-status states. */
  state: string;
  /** One of x-status reasons. */
  reason: string;
  /** The line every view shows for it, as it is. */
  text: string;
  /** When it ends by itself (a clock, a wall's reset); null when nothing says. */
  until: Timestamp | null;
  /** Since when it has been so; null when not known. */
  since: Timestamp | null;
  /** The tasks it waits on, for reason task; empty otherwise. */
  tasks: number[];
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

export type DaemonStatus = {
  alive: boolean;
  /** The loaded build's identity: its git sha, or <version>+<sha>[-dirty] for a release build. */
  version: string;
  /** The absolute path of the executable this process runs. */
  exe?: string;
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
  /** Null until the first measure, and on a daemon that does not measure. */
  resources?: Resources | null;
  /** The machine's own needs-you items, oldest first; absent when there are none, and on a daemon that does not serve it. A client draws each beside the agents' needs-you cards, in the text given. */
  needs_you?: MachineNeed[];
};

export type Repo = {
  name: string;
  path: string;
  default_branch: string;
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
  /** Without settings.get `agent`: the built-in handoff prompt, what `values.handoff_prompt` shows while none is set, so a client can tell the two apart. */
  handoff_prompt_default?: string;
  /** Without settings.get `agent`: the agents whose agent.yaml sets a handoff prompt of their own, which the global one does not reach. */
  handoff_prompt_own?: AgentName[];
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

export type Capabilities = {
  chat: boolean;
  /** Worktree review beyond worktree.diff; false in v1, the UI hides the Review tab. */
  review: boolean;
  /** Formats the daemon serves on at least one host. */
  terminal_format: ("text" | "bytes" | "styled")[];
  /** Each terminal host the daemon has and the formats it serves there, e.g. {tmux: [text, bytes]}. Absent: text on the one host it has. */
  terminal_hosts?: { [key: string]: string[] };
  /** The daemon serves the transcript topic and transcript.events. Absent means false: a client then watches the terminal. */
  transcript?: boolean;
  /** The founder's own messages to a terminal agent are typed into its terminal (unless its agent.yaml turns peer_inbox off), so hl launch tells the agent so. Absent means false. */
  founder_typed?: boolean;
};

export type Listener = {
  enabled: boolean;
  /** loopback, lan, tailscale or an address. */
  bind: string;
  /** Always an encrypted WebSocket with a certificate; `none` only on loopback. A Tailscale address is just a bind address. */
  encryption: "tls" | "none";
  fingerprint: string | null;
};

/** Chosen by the caller; unique per intended action. */
export type RequestId = string;

/** Every global setting by its config.yaml name, with its default; every one is the founder's to set. Per-account keys (label, cap, usage recovery, cycle) are account.set; per-agent keys are agent.set and schedule.*; a key marked Per agent also takes `agent` in settings.set. */
export type SettingsValues = {
  /** Default cap: most agents mid-turn at once on an account with no cap of its own. */
  active_cap?: number;
  /** Idle close, seconds; 0 never. The UI shows whole minutes. Per agent with `agent`. */
  park_timeout_s?: number;
  /** Agents write a note for the next session before a close or move. */
  handoff_note?: boolean;
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
  /** Seconds after the first waiting notice before the agent is typed one digest, at its prompt. Per agent. */
  notice_digest_s?: number;
  /** Input price per million tokens by model-name pattern, laid over the built-in table; an entry that is not a positive number is ignored. */
  token_prices?: { [key: string]: number };
  /** NEW, no code today: seconds a person or device may hold a terminal's input without typing before the daemon takes it back (see terminal `holder` notice). */
  input_hold_idle_s?: number;
  /** The prompt typed at an agent to write its handoff note, one line; {path} is the note's file and {max} its size limit (3000). Without {path} the path is added on a line of its own. Line breaks are joined into one space; under 20 characters after trimming is bad_request (blank means the built-in). settings.get shows the built-in prompt when none is set. Per agent. */
  handoff_prompt?: string;
  /** Whether a notifications `prompt_asked` event (an agent asks (a permission or a question)) raises a notice on the founder's devices. The event is on the topic and in events.feed either way; the shell that raises notices reads this. */
  push_prompt_asked?: boolean;
  /** Whether a notifications `task_done` event (a task is done) raises a notice on the founder's devices. The event is on the topic and in events.feed either way; the shell that raises notices reads this. */
  push_task_done?: boolean;
  /** Whether a notifications `stall` event (an agent stalls) raises a notice on the founder's devices. The event is on the topic and in events.feed either way; the shell that raises notices reads this. */
  push_stall?: boolean;
  /** Whether a notifications `account_out` event (an account is out) raises a notice on the founder's devices. The event is on the topic and in events.feed either way; the shell that raises notices reads this. */
  push_account_out?: boolean;
  /** Most agents mid-turn at once on this machine, over every account; 0 is no limit. Over it the daemon starts no new work (status reason load). */
  host_active_cap?: number;
  /** Most 1-minute load per core, in percent (150 is 1.5 per core); 0 is off. Over it the daemon starts no new work. */
  load_per_core_pct?: number;
  /** Least free memory, in percent of all; 0 is off. Under it the daemon starts no new work. */
  ram_free_min_pct?: number;
  /** Least free disk on the volume the daemon home is on, in GB; 0 is off. Under it heavy runs (builds and tests, tools/heavy-slot.sh) wait; agent turns are never held. It is also the floor the disk needs-you item uses. */
  disk_free_min_gb?: number;
  /** 1 holds the daemon's new work while a phone simulator or emulator runs on this machine (a booted iOS simulator device, or the qemu an Android emulator runs on); 0 does not. Held the same way as a load limit (status reason load), by the same rules. */
  host_simulator_hold?: number;
  /** Hold each account to its weekly pace line (Account.pace): an account ahead of the line starts fewer turns, and one ahead by more than pace_burst_pct starts none (status reason pace) until the line catches up or another account has room. A turn a person starts is never held, and a running turn is never stopped. Off: only the windows' walls hold work. */
  pacing?: boolean;
  /** Where each account's pace line ends: its weekly usage, in percent, at the end of its week (its weekly reset, or its subscription end when that comes first). Under 100 because a reading lags by minutes. */
  pace_target_pct?: number;
  /** How far, in points of the week, an account may run ahead of its pace line. Inside it the account's cap shrinks in step; past it the account starts no new turn. */
  pace_burst_pct?: number;
  /** How many heavy runs (test and build slots, tools/heavy-slot.sh) may hold a slot at once on this machine. The count is read from the config.yaml of the home the process runs in, and the slots are one lock per machine: two homes on one machine share it, so set the same number in both. HANDLINE_HEAVY_SLOTS in the environment overrides it. A change reaches the next run without a restart. */
  heavy_slots?: number;
  /** Keep the machine from idle sleep: off; on (held while the daemon runs); working (held while an agent is mid-turn, released when the last one stops). The daemon holds macOS caffeinate -i; closing the lid still sleeps. Applied at the next tick, without a restart. */
  keep_awake?: "off" | "on" | "working";
  /** What the daemon does with work its agents left running after its parent died (headless Chrome, vitest, vite, a go test binary, yes, Electron) that has run 30 minutes under a worktree: dry (the default) only logs and records an orphan_would_stop event every 10 minutes; on stops it and records orphan_stopped; off does nothing. Nothing outside a worktree is touched. */
  reap_orphans?: "dry" | "on" | "off";
};

/** settings.set's changes: any of SettingsValues' keys; null removes one. */
export type SettingsChanges = {
  /** Default cap: most agents mid-turn at once on an account with no cap of its own. null removes the key (back to the default). */
  active_cap?: number | null;
  /** Idle close, seconds; 0 never. The UI shows whole minutes. Per agent with `agent`. null removes the key (back to the default). */
  park_timeout_s?: number | null;
  /** Agents write a note for the next session before a close or move. null removes the key (back to the default). */
  handoff_note?: boolean | null;
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
  /** Seconds after the first waiting notice before the agent is typed one digest, at its prompt. Per agent. null removes the key (back to the default). */
  notice_digest_s?: number | null;
  /** Input price per million tokens by model-name pattern, laid over the built-in table; an entry that is not a positive number is ignored. null removes the key (back to the default). */
  token_prices?: { [key: string]: number } | null;
  /** NEW, no code today: seconds a person or device may hold a terminal's input without typing before the daemon takes it back (see terminal `holder` notice). null removes the key (back to the default). */
  input_hold_idle_s?: number | null;
  /** The prompt typed at an agent to write its handoff note, one line; {path} is the note's file and {max} its size limit (3000). Without {path} the path is added on a line of its own. settings.get shows the built-in prompt when none is set. Per agent. An empty string is the built-in prompt; null removes the key (back to the default). */
  handoff_prompt?: string | null;
  /** Whether a notifications `prompt_asked` event (an agent asks (a permission or a question)) raises a notice on the founder's devices. The event is on the topic and in events.feed either way; the shell that raises notices reads this. null removes the key (back to the default). */
  push_prompt_asked?: boolean | null;
  /** Whether a notifications `task_done` event (a task is done) raises a notice on the founder's devices. The event is on the topic and in events.feed either way; the shell that raises notices reads this. null removes the key (back to the default). */
  push_task_done?: boolean | null;
  /** Whether a notifications `stall` event (an agent stalls) raises a notice on the founder's devices. The event is on the topic and in events.feed either way; the shell that raises notices reads this. null removes the key (back to the default). */
  push_stall?: boolean | null;
  /** Whether a notifications `account_out` event (an account is out) raises a notice on the founder's devices. The event is on the topic and in events.feed either way; the shell that raises notices reads this. null removes the key (back to the default). */
  push_account_out?: boolean | null;
  /** Most agents mid-turn at once on this machine, over every account; 0 is no limit. Over it the daemon starts no new work (status reason load). null removes the key (back to the default). */
  host_active_cap?: number | null;
  /** Most 1-minute load per core, in percent (150 is 1.5 per core); 0 is off. Over it the daemon starts no new work. null removes the key (back to the default). */
  load_per_core_pct?: number | null;
  /** Least free memory, in percent of all; 0 is off. Under it the daemon starts no new work. null removes the key (back to the default). */
  ram_free_min_pct?: number | null;
  /** Least free disk on the volume the daemon home is on, in GB; 0 is off. Under it heavy runs (builds and tests, tools/heavy-slot.sh) wait; agent turns are never held. It is also the floor the disk needs-you item uses. null removes the key (back to the default). */
  disk_free_min_gb?: number | null;
  /** 1 holds the daemon's new work while a phone simulator or emulator runs on this machine; 0 does not. null removes the key (back to the default). */
  host_simulator_hold?: number | null;
  /** Hold each account to its weekly pace line (Account.pace): an account ahead of the line starts fewer turns, and one ahead by more than pace_burst_pct starts none (status reason pace) until the line catches up or another account has room. A turn a person starts is never held, and a running turn is never stopped. Off: only the windows' walls hold work. null removes the key (back to the default). */
  pacing?: boolean | null;
  /** Where each account's pace line ends: its weekly usage, in percent, at the end of its week (its weekly reset, or its subscription end when that comes first). Under 100 because a reading lags by minutes. null removes the key (back to the default). */
  pace_target_pct?: number | null;
  /** How far, in points of the week, an account may run ahead of its pace line. Inside it the account's cap shrinks in step; past it the account starts no new turn. null removes the key (back to the default). */
  pace_burst_pct?: number | null;
  /** How many heavy runs (test and build slots, tools/heavy-slot.sh) may hold a slot at once on this machine. The count is read from the config.yaml of the home the process runs in, and the slots are one lock per machine: two homes on one machine share it, so set the same number in both. HANDLINE_HEAVY_SLOTS in the environment overrides it. A change reaches the next run without a restart. null removes the key (back to the default). */
  heavy_slots?: number | null;
  /** Keep the machine from idle sleep: off; on (held while the daemon runs); working (held while an agent is mid-turn, released when the last one stops). The daemon holds macOS caffeinate -i; closing the lid still sleeps. Applied at the next tick, without a restart. null removes the key (back to the default). */
  keep_awake?: ("off" | "on" | "working") | null;
  /** What the daemon does with work its agents left running after its parent died (headless Chrome, vitest, vite, a go test binary, yes, Electron) that has run 30 minutes under a worktree: dry (the default) only logs and records an orphan_would_stop event every 10 minutes; on stops it and records orphan_stopped; off does nothing. Nothing outside a worktree is touched. */
  reap_orphans?: ("dry" | "on" | "off") | null;
};

/** One method as a daemon lists it: its role and whether this caller may call it. */
export type MethodInfo = {
  name: string;
  since: number;
  role: Role;
  allowed: boolean;
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

/** Chosen by the daemon when an upload completes. */
export type AttachmentId = string;

/** One pane of the terminal host, with what the daemon knows of it. A field the daemon does not know is null. */
export type DebugTerminalPane = {
  session: string;
  /** The window's index in its session. */
  window: string;
  window_name: string;
  /** The pane's handle, as terminal.attach takes it. */
  terminal: string;
  /** The pane's own process (its shell). */
  pid: number | null;
  /** What the host says runs in the pane now. */
  command: string;
  path: string | null;
  dead: boolean;
  last_activity: Timestamp | null;
  /** Seconds since the pane's window last drew output. */
  idle_s: number | null;
  /** agent: an agent CLI (claude, codex, opencode) runs at or under the pane's process; shell: none does and the pane shows a shell; unknown: the process table could not be read and the pane's command says neither. */
  process: "agent" | "shell" | "other" | "unknown";
  agent_process: {
    pid: number;
    name: string;
  } | null;
  /** The agent whose current terminal this pane is. */
  agent: AgentName | null;
  /** No agent has this pane as its terminal. */
  orphan: boolean;
  /** The agent is closed; it keeps the handle of the pane it had. */
  closed: boolean;
  /** For an orphan: the agent the daemon opened the pane for. */
  recorded_agent: AgentName | null;
  state: string | null;
  account: string | null;
  session_id: string | null;
  worktree: string | null;
  /** When the agent's prompt cache lapses, from its view. */
  cache_until: Timestamp | null;
  /** Always null: the daemon keeps no link from a tmux pane to an Orca terminal. */
  orca_terminal: null;
};

/** Words of a task's text between its image references. */
export type TextPart = {
  text: string;
};

/** An image a task's text names, where it names it. */
export type ImagePart = {
  /** The Attachment; only `id` when the image is gone. */
  image: {
    id: AttachmentId;
    name?: string;
    /** As Attachment.mime. */
    mime?: string;
    bytes?: number;
  };
  /** The words in the reference's brackets; what a client shows when it cannot draw the image. */
  caption: string;
  /** true when the image is no longer kept: the client shows the caption without a picture. */
  gone?: boolean;
  /** Where the image is stored. Only for an agent caller, which reads it by path; a member or founder client never gets it. */
  path?: string;
};

/** The request's ref: 12 lowercase hex characters, unique and not guessable from another request's. The integer id stays the request's key everywhere else. */
export type RequestRef = string;

/** How this process is reached, read only. */
export type ConnectionInfo = {
  /** Which process answered. A daemon behind a gateway has no listener of its own; ask the gateway's local socket for the gateway's. */
  served_by: "daemon" | "gateway";
  /** The network listener of this process; null when it has none. */
  listener: null | {
    /** The host:port a pairing names (-advertise, else where the listener binds). */
    address: string;
    /** none is Tailscale mode: plain ws on a Tailscale or loopback address. */
    encryption: "tls" | "none";
    /** sha256 of the certificate presented; null in Tailscale mode. */
    fingerprint: string | null;
    /** The address's host when it is a *.ts.net name, else null. */
    tailscale_host: string | null;
  };
  heavy_slots: {
    /** HANDLINE_HEAVY_SLOTS as this process sees it, default 2. */
    slots: number;
    /** Where the slot locks are kept: HANDLINE_HEAVY_DIR, else ~/.handline/heavy-slots. */
    dir: string | null;
  };
  /** SettingsValues.dev_mode. */
  dev_mode: boolean;
};

/** A request named by its integer id, or by its ref: msg_ and the 12 hex characters, or the hash alone, or a prefix of at least 6 of them that begins only one request the caller may read (an ambiguous one is bad_request). A request the caller may not read is not_found, the same answer as one that does not exist. */
export type RequestName = number | string;

/** What the machine and each agent use, measured every 15 s: one ps read for every agent. A figure the daemon could not read is 0. */
export type Resources = {
  /** When it was measured. */
  at: Timestamp;
  /** Per agent with a terminal pane: its pane process and every process under it. */
  agents: { [key: string]: {
    /** CPU in percent of one core, summed over the tree (ps %cpu): over 100 on several cores. */
    cpu_pct: number;
    /** Resident memory summed over the tree. */
    rss_bytes: number;
  } };
  machine: {
    ram_total_bytes: number;
    ram_used_bytes: number;
    /** Free for use: macOS free, inactive and speculative pages; Linux MemAvailable. */
    ram_free_bytes: number;
    /** The 1-minute load average; compare with cores. */
    load_1m: number;
    cores: number;
    /** Free space of the volume the daemon home is on. */
    disk_free_bytes: number;
    disk_total_bytes: number;
    /** load_1m divided by cores; the load limit is compared with this (150 percent is 1.5). */
    load_per_core: number;
    /** ram_free_bytes in percent of ram_total_bytes; the free-memory floor is compared with this. */
    ram_free_pct: number;
    /** Heavy slots held now (tools/heavy-slot.sh), at the same measure. Light slots are not counted. The cap is settings.get heavy_slots, or connection.info heavy_slots.slots when the environment overrides it. */
    heavy_running: number;
  };
  /** What all agents with a terminal pane use together. */
  agents_total: {
    /** CPU in percent of one core, summed over every agent's tree. */
    cpu_pct: number;
    /** Resident memory summed over every agent's tree. */
    rss_bytes: number;
  };
  /** Why the daemon starts no new work now (the machine is over a load limit: text names the limit, as the status reason load does), null when starts may go. Level-triggered on the same measure; a machine with no agent mid-turn never holds. */
  start_hold: string | null;
};

/** How the person's agent list and dock show agents, the same on every device. */
export type NavPrefs = {
  /** Pinned agents in the person's order: the dock shows them first and the list on top. No name twice. */
  pins: AgentName[];
  /** The list shows only agents with something unread or waiting on the person. */
  unread_only: boolean;
  /** State groups the list hides: needs (waiting, blocked), working, waking, idle, sleeping (parked, none). */
  hidden: ("needs" | "working" | "waking" | "idle" | "sleeping")[];
  /** tree: team, lead, members by parent. flat: urgency first, then by name. */
  layout: "tree" | "flat";
  /** Agents folded in the tree. */
  collapsed: AgentName[];
  /** The person's order for the tree layout: agents under one parent, and the top-level agents, stand in this order; one not listed (a new agent) comes after the listed ones, by name. The flat layout ignores it. No name twice. */
  order: AgentName[];
};

/** Any keys of NavPrefs; each given key replaces the stored value whole. */
export type NavPrefsChanges = {
  /** Pinned agents in the person's order: the dock shows them first and the list on top. No name twice. */
  pins?: AgentName[];
  /** The list shows only agents with something unread or waiting on the person. */
  unread_only?: boolean;
  /** State groups the list hides: needs (waiting, blocked), working, waking, idle, sleeping (parked, none). */
  hidden?: ("needs" | "working" | "waking" | "idle" | "sleeping")[];
  /** tree: team, lead, members by parent. flat: urgency first, then by name. */
  layout?: "tree" | "flat";
  /** Agents folded in the tree. */
  collapsed?: AgentName[];
  /** The person's order for the tree layout: agents under one parent, and the top-level agents, stand in this order; one not listed (a new agent) comes after the listed ones, by name. The flat layout ignores it. No name twice. */
  order?: AgentName[];
};

/** The person's view preferences, one set for the founder's devices, kept by the daemon. A new daemon serves the defaults: no pins, unread_only false, hidden empty, layout tree, collapsed empty, order empty. */
export type Prefs = {
  nav: NavPrefs;
};

export type ReadCursor = {
  /** The newest stream cursor when this read began. An event above it may be in the answer already (applying it again changes nothing); none at or below it is missing. A client drops the events at or below it for this answer. */
  cursor: number;
};

/** Something the machine needs of the founder, which no agent has: not a task and not an inbox message. */
export type MachineNeed = {
  /** disk: free space is under the floor (config disk_free_min_gb, default 5), so builds and tests wait. It stays until space is back over the floor plus 2 GB. */
  kind: "disk";
  since: Timestamp;
  /** The item in plain words, with the free space in it. */
  text: string;
  /** disk only, when there are any: the worktrees no agent works in (worktrees.list orphan), as housekeeping last measured them (at most hourly). The text names them too. */
  orphan_worktrees?: {
    count: number;
    size_bytes: number;
  };
};

export interface CoreMethods {
  "hello": { params: {
  /** name/version of the caller. */
  client: string;
  /** The protocol the client will speak. */
  protocol: number;
  /** Device token; network listener only. */
  token?: string;
  /** The founder key, from the founder's own key file; local socket only. Wrong key: refused, gate `role`. */
  key?: string;
  /** The git sha the client was built from. When it is not the daemon's own, the daemon logs it once on this connection; nothing is refused. */
  client_build?: string;
}; result: {
    /** Newest protocol the daemon speaks. */
    protocol: number;
    /** Oldest client protocol it still serves. */
    min_client: number;
    /** The loaded build's identity: its git sha, or <version>+<sha>[-dirty] for a release build. */
    daemon: string;
    /** The same build identity as `daemon`, under the name daemon.status uses, so a client compares its own build with one field. */
    version?: string;
    /** The absolute path of the executable this process runs. */
    exe?: string;
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
  /** replay (the default): what was missed after `since` is sent first, as `since` says. latest: nothing missed is sent, neither a replay nor a gap; the reply's cursor is the newest and only later events follow. A client that resumes at the latest reads again what it shows: each read a stream event keeps current says its `cursor`, and an event at or below it is in that answer already. */
  resume?: "replay" | "latest";
  /** Only events of this agent, plus events that carry no agent. Meant for the chat topic. */
  agent?: AgentName;
}; result: {
    /** The newest cursor at subscribe time. */
    cursor: number;
  } };
  "unsubscribe": { params: {
  topics: Topic[];
}; result: { [key: string]: unknown } };
  "daemon.status": { params: { [key: string]: unknown }; result: DaemonStatus & ReadCursor };
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
}; result: Operation & ReadCursor };
  "debug.terminals": { params: { [key: string]: unknown }; result: {
    host: string;
    now: Timestamp;
    panes: DebugTerminalPane[];
  } };
  "prefs.get": { params: { [key: string]: unknown }; result: Prefs & ReadCursor };
  "prefs.set": { params: {
  changes: {
    nav: NavPrefsChanges;
  };
  request_id: RequestId;
}; result: Accepted };
  "hook.record": { params: {
  agent: AgentName;
  kind: string;
  at: Timestamp;
  data: { [key: string]: unknown };
  request_id: RequestId;
}; result: Accepted };
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
  "connection.get": { params: { [key: string]: unknown }; result: ConnectionInfo };
  "daemon.upgrade": { params: {
  /** Absolute path of the handlined to run. */
  exe: string;
  /** The git sha that binary was built from. */
  sha: string;
  request_id: RequestId;
}; result: {
    /** The build answering this call. */
    from: string;
    /** The sha it restarts onto. */
    to: string;
    exe: string;
  } };
  "settings.get": { params: {
  /** Return that agent's own overrides in `overrides`. */
  agent?: AgentName;
}; result: Settings };
  "settings.set": { params: {
  changes: SettingsChanges;
  /** Write the per-agent override of: park_timeout_s, ignore_cache_expiry, bg_notice_s, bg_grace_s, bg_check_s, notice_digest_s, handoff_prompt. Any other key with `agent` is bad_request. */
  agent?: AgentName;
  request_id: RequestId;
}; result: Accepted };
}

export const CORE_METHODS: { [K in keyof CoreMethods]: { role: Role; callers: Role[] | null; write: boolean; since: number } } = {
  "hello": { role: "anonymous", callers: null, write: false, since: 1 },
  "whoami": { role: "agent", callers: null, write: false, since: 1 },
  "subscribe": { role: "agent", callers: null, write: false, since: 1 },
  "unsubscribe": { role: "agent", callers: null, write: false, since: 1 },
  "daemon.status": { role: "agent", callers: null, write: false, since: 1 },
  "events.feed": { role: "agent", callers: null, write: false, since: 1 },
  "operation.get": { role: "member", callers: null, write: false, since: 1 },
  "debug.terminals": { role: "member", callers: null, write: false, since: 1 },
  "prefs.get": { role: "member", callers: ["member", "founder"], write: false, since: 1 },
  "prefs.set": { role: "member", callers: ["member", "founder"], write: true, since: 1 },
  "hook.record": { role: "agent", callers: ["agent"], write: true, since: 1 },
  "memory.status": { role: "agent", callers: null, write: false, since: 1 },
  "connection.get": { role: "founder", callers: null, write: false, since: 1 },
  "daemon.upgrade": { role: "founder", callers: null, write: true, since: 1 },
  "settings.get": { role: "founder", callers: null, write: false, since: 1 },
  "settings.set": { role: "founder", callers: null, write: true, since: 1 },
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
export const STATE_WORDS = ["running", "idle", "queued", "starting", "parked", "waiting", "stalled", "blocked", "no_terminal"] as const;

/** x-status: the state and reason every agent and task carries; open sets for a client, which shows `text` for a word it does not know. */
export const STATUS_STATES = ["preparing", "starting", "running", "waiting", "stopped", "idle", "off", "done"] as const;
export const STATUS_REASONS = ["work", "restart", "retry", "launch", "turn", "watch", "handoff", "background", "you", "opener", "task", "owner", "clock", "move", "load", "pace", "wall", "cap", "stalled", "none", "done", "discarded"] as const;

export const ROLE_ORDER: Role[] = ["anonymous", "agent", "member", "founder"];
