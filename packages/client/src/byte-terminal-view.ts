import type { InputHolder, Notifications, TerminalBytesSnapshot } from './protocol.ts';

export type ByteViewState = 'attaching' | 'live' | 'stale' | 'failed';
/** What a pane does with the stream: `reset` repaints (reset, resize to cols by rows, write data in one step), `write` appends. */
export type ByteEvent =
  | { type: 'reset'; data: Uint8Array; cols: number; rows: number; reason: 'attach' | 'resize' | 'slow_client' }
  | { type: 'write'; data: Uint8Array }
  | { type: 'marker'; marker: Notifications['marker'] }
  | { type: 'state' };

export type ByteListener = (e: ByteEvent, view: ByteTerminalView) => void;

export const fromBase64 = (s: string): Uint8Array => Uint8Array.from(atob(s), (ch) => ch.charCodeAt(0));
const CHUNK = 0x8000; // spreading more than this into one call overflows the stack
export const toBase64 = (b: Uint8Array): string => {
  let bin = '';
  for (let i = 0; i < b.length; i += CHUNK) bin += String.fromCharCode(...b.subarray(i, i + CHUNK));
  return btoa(bin);
};

/** A terminal watched as bytes: the stream in order, with the attach's one seq counter. The pane owns the screen. */
export class ByteTerminalView {
  readonly agent: string;
  terminal = '';
  seq = 0;
  cols = 0;
  rows = 0;
  altScreen = false;
  state: ByteViewState = 'attaching';
  failure = ''; // why the last attach failed, while state is `failed`
  holders: InputHolder[] = [];
  history: string[] = [];
  historyFirst = 0;
  historyMore = false;
  private listeners = new Set<ByteListener>();
  constructor(agent: string) {
    this.agent = agent;
  }

  subscribe(fn: ByteListener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  setState(state: ByteViewState, failure = ''): void {
    this.state = state;
    this.failure = failure;
    this.emit({ type: 'state' });
  }

  /** The attach reply: everything the stream said so far, as one repaint. */
  setSnapshot(s: TerminalBytesSnapshot): void {
    this.terminal = s.terminal;
    this.holders = s.holders;
    this.history = s.history.lines;
    this.historyFirst = s.history.first;
    this.historyMore = s.history.more;
    this.repaint(s, 'attach');
    this.setState('live');
  }

  /** Applies one stream notice. `resync` means a notice was missed and the view must attach again. */
  apply(method: 'output' | 'snapshot' | 'marker' | 'holder', n: { seq: number }): 'applied' | 'ignored' | 'resync' {
    if (n.seq <= this.seq) return 'ignored'; // already in the snapshot, or a repeat
    if (n.seq !== this.seq + 1) return 'resync';
    this.seq = n.seq;
    if (method === 'output') this.emit({ type: 'write', data: fromBase64((n as Notifications['output']).data) });
    else if (method === 'snapshot') {
      const s = n as Notifications['snapshot'];
      this.repaint(s, s.reason);
    } else if (method === 'marker') this.emit({ type: 'marker', marker: n as Notifications['marker'] });
    else {
      this.holders = (n as Notifications['holder']).holders;
      this.emit({ type: 'state' });
    }
    return 'applied';
  }

  /** Scrollback lines (SGR-coded strings) older than what is held, as `terminal.history` returns them. */
  prependHistory(first: number, lines: string[], more: boolean): void {
    this.history = [...lines, ...this.history];
    this.historyFirst = first;
    this.historyMore = more;
    this.emit({ type: 'state' });
  }

  private repaint(
    s: { seq: number; cols: number; rows: number; data: string; alt_screen: boolean },
    reason: 'attach' | 'resize' | 'slow_client',
  ) {
    this.seq = s.seq;
    this.cols = s.cols;
    this.rows = s.rows;
    this.altScreen = s.alt_screen;
    this.emit({ type: 'reset', data: fromBase64(s.data), cols: s.cols, rows: s.rows, reason });
  }
  private emit(e: ByteEvent): void {
    for (const fn of this.listeners) fn(e, this);
  }
}
