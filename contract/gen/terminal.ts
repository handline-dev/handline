// Generated from contract/spec/ by contract/generate.py; do not edit.
/* eslint-disable */
import type { Accepted, AgentName, Cursor, InputHolder, RequestId, Role, Row } from './core.ts';

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

export type HistoryPage = {
  first: number;
  lines: Row[];
  more: boolean;
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

export type TerminalOpening = {
  agent: AgentName;
  /** The terminal handle, as terminal.attach takes it. */
  terminal: string;
  /** tmux; open set. */
  host: string;
  attach: {
    argv: string[];
  } | null;
  focused: boolean;
  local: boolean;
};

export type ShellOpening = {
  agent: AgentName;
  /** The shell's handle, as terminal.attach takes it. */
  terminal: string;
  /** The directory the shell started in. */
  cwd: string;
  host: string;
};

export interface TerminalMethods {
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
  "shell.open": { params: {
  agent: AgentName;
  command?: string;
  block?: string;
  request_id: RequestId;
}; result: ShellOpening };
  "shell.close": { params: {
  /** The handle shell.open answered. */
  terminal: string;
  request_id: RequestId;
}; result: Accepted };
  "terminal.detach": { params: {
  terminal: string;
  /** With it, ends only that format's attach of the terminal on this connection; without it, every attach of that terminal on the connection. Neither is an error when there is nothing to end; any other word is bad_request naming `format`. */
  format?: "text" | "bytes" | "styled";
}; result: { [key: string]: unknown } };
}

export const TERMINAL_METHODS: { [K in keyof TerminalMethods]: { role: Role; callers: Role[] | null; write: boolean; since: number } } = {
  "terminal.attach": { role: "member", callers: null, write: false, since: 1 },
  "terminal.history": { role: "member", callers: null, write: false, since: 1 },
  "terminal.resize": { role: "member", callers: null, write: true, since: 1 },
  "terminal.input": { role: "member", callers: null, write: true, since: 1 },
  "terminal.close": { role: "member", callers: null, write: true, since: 1 },
  "terminal.open": { role: "member", callers: null, write: false, since: 1 },
  "shell.open": { role: "member", callers: null, write: true, since: 1 },
  "shell.close": { role: "member", callers: null, write: true, since: 1 },
  "terminal.detach": { role: "member", callers: null, write: false, since: 1 },
};
