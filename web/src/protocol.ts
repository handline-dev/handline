// Hand-written from docs/daemon-plan.md (Protocol, Event stream, Session control, Terminal channel).
// Replaced by the types generated from contract/ (plan task 1) when they land; keep every wire name
// in this one file so that is a swap. Where the plan is silent the choice is marked ASSUMED.

export const PROTOCOL = 1;

export type Role = 'founder' | 'member' | 'agent';

// `stale_prompt` is the plan's reply to prompt.answer on a changed prompt (ASSUMED: carried as a code).
export type ErrorCode =
  | 'not_found' | 'refused' | 'daemon_busy' | 'bad_request' | 'internal' | 'stale_prompt';

export type Topic = 'agents' | 'tasks' | 'requests' | 'usage';

// Founder decision (T1686): v1 has a chat view beside the terminal. Its messages and methods are not
// in the plan yet; this is the room left for them, to be filled from term-design's data list.
export interface ChatMessage {
  id: string; agent: string; role: 'user' | 'assistant' | 'tool' | 'system'; text: string; time: string;
  data?: Record<string, unknown>; // tool name, input, result, as the contract defines
}

export type NamedKey =
  | 'esc' | 'enter' | 'up' | 'down' | 'tab' | 'shift-tab' | 'ctrl-c' | '0' | '1' | '2' | '3' | '4' | '5'
  | '6' | '7' | '8' | '9';

export type AgentState = 'running' | 'idle' | 'waiting' | 'blocked' | 'parked' | 'no_terminal';

// ---- JSON-RPC 2.0 framing: one JSON object per line (socket) or per message (WebSocket) ----

export interface RpcRequest { jsonrpc: '2.0'; id: number | string; method: string; params?: unknown }
export interface RpcNotification { jsonrpc: '2.0'; method: string; params?: unknown }
export interface RpcErrorBody {
  code: number; // JSON-RPC numeric code
  message: string; // a sentence a person can read
  data?: { code: ErrorCode; reason?: string }; // ASSUMED home of the stable string code
}
export interface RpcResponse {
  jsonrpc: '2.0'; id: number | string | null; result?: unknown; error?: RpcErrorBody;
}

// ---- handshake ----

export interface HelloParams { client: string; protocol: number }
export interface HelloResult {
  protocol: number;
  min_client: number;
  daemon: string; // loaded git sha
  schema: number;
  role: Role;
  methods: Record<string, number>; // method -> protocol it appeared in; only those this role may call
}

// ---- views ----

export interface AgentView {
  name: string;
  role: string;
  model: string;
  effort: string;
  state: AgentState;
  since: string; // ISO time
  account: string | null;
  task: string | null;
  has_dialog: boolean;
  parent: string | null;
}
// who: 'daemon' or a person/device. Carried as a list (length 1 in v1) so several holders need no protocol change.
export interface InputHolder { who: string; since: string }

export interface Prompt {
  id: string;
  kind: 'permission' | 'dialog';
  agent: string;
  tool: string | null;
  command: string | null;
  reason: string | null;
  lines: string[];
  options: { key: NamedKey; label: string }[]; // ASSUMED: "offered keys" with a label; choice is a key
}
export interface AgentDetail extends AgentView {
  worktree: string;
  branch: string;
  terminal: string; // terminal handle
  session: string | null; // session id when a session exists
  session_open: boolean;
  input: InputHolder[];
  prompt: Prompt | null;
}
export interface DaemonStatus {
  alive: boolean; version: string; protocol: number; host: { name: string; healthy: boolean };
  counts: Partial<Record<AgentState, number>>; inbox: number;
}

// ---- event stream ----

export interface EventNotice {
  cursor: number; // events row id; only grows
  topic: Topic;
  kind: string; // e.g. state, prompt_open, prompt_closed
  agent?: string;
  actor?: string;
  data?: Record<string, unknown>;
}
export interface GapNotice { cursor: number } // refetch, then resume from cursor

// ---- terminal channel: text rows (a styled-row slot is reserved, not used) ----

export interface TerminalSnapshot {
  terminal: string;
  seq: number;
  cols: number;
  rows: number;
  frame: string[]; // exactly `rows` lines
  history: string[]; // one page of scrollback, oldest first
  history_more: boolean;
}
// ASSUMED shape of "frame {seq, changed rows}": `scrolled` lines left the top before `rows` apply.
export interface FrameNotice {
  terminal: string; seq: number; scrolled: number; rows: { row: number; text: string }[];
}
export interface ResizeNotice { terminal: string; seq: number; cols: number; rows: number }
export interface MarkerNotice { terminal: string; seq: number; row: number; kind: 'daemon_typed' | 'control'; text: string }

// ---- methods ----

export type SessionStopMode = 'exit' | 'close' | 'kill';

type WriteBase = { request_id?: string }; // the client fills it in; a retry reuses it

export interface Methods {
  'hello': { params: HelloParams; result: HelloResult };
  'whoami': { params: Record<string, never>; result: { viewer: string; role: Role; methods: string[] } };
  'daemon.status': { params: Record<string, never>; result: DaemonStatus };
  'agents.list': { params: Record<string, never>; result: { agents: AgentView[] } };
  'agent.get': { params: { agent: string }; result: AgentDetail };
  'subscribe': { params: { topics: Topic[]; since?: number }; result: { cursor: number } };
  'unsubscribe': { params: Record<string, never>; result: Record<string, never> };

  'session.start': { params: WriteBase & { agent: string; account?: string }; result: { accepted: true } };
  'session.wake': { params: WriteBase & { agent: string }; result: { accepted: true } };
  'session.stop': { params: WriteBase & { agent: string; mode: SessionStopMode }; result: { accepted: true } };
  'session.input': { params: WriteBase & { agent: string; text: string; enter: boolean; prompt?: never }; result: { accepted: true } };
  'session.keys': { params: WriteBase & { agent: string; keys: NamedKey[] }; result: { accepted: true } };
  'session.interrupt': { params: WriteBase & { agent: string }; result: { accepted: true } };
  'prompt.list': { params: { agent: string }; result: { prompts: Prompt[] } };
  'prompt.answer': { params: WriteBase & { prompt: string; choice: NamedKey }; result: { accepted: true } };
  'input.take': { params: WriteBase & { agent: string }; result: { holders: InputHolder[] } };
  'input.release': { params: WriteBase & { agent: string }; result: { holders: InputHolder[] } };

  'terminal.attach': { params: { agent: string }; result: TerminalSnapshot };
  'terminal.detach': { params: { terminal: string }; result: Record<string, never> }; // ASSUMED: plan names no detach call
  'terminal.history': { params: { terminal: string; before: number; limit: number }; result: { lines: string[]; more: boolean } };
  'terminal.resize': { params: WriteBase & { terminal: string; cols: number; rows: number }; result: { accepted: true } };
}
export type MethodName = keyof Methods;

// Methods that change something and so carry a request_id.
export const WRITE_METHODS: ReadonlySet<string> = new Set([
  'session.start', 'session.wake', 'session.stop', 'session.input', 'session.keys', 'session.interrupt',
  'prompt.answer', 'input.take', 'input.release', 'terminal.resize',
]);

// Lowest role allowed per method; agents are barred from the person-at-the-terminal methods.
export const BARRED_FOR_AGENT: ReadonlySet<string> = new Set([
  'session.input', 'session.keys', 'session.interrupt', 'prompt.answer', 'input.take', 'input.release',
  'terminal.attach', 'terminal.detach', 'terminal.history', 'terminal.resize',
]);

// Notification names the daemon pushes.
export type Notice =
  | { method: 'event'; params: EventNotice }
  | { method: 'gap'; params: GapNotice }
  | { method: 'frame'; params: FrameNotice }
  | { method: 'resize'; params: ResizeNotice }
  | { method: 'marker'; params: MarkerNotice };
