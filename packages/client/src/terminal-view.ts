import type { InputHolder, Notifications, TerminalSnapshot } from './protocol.ts';

/** What a terminal pane renders: the screen rows, the scrollback fetched so far, and who holds the input. */
export class TerminalView {
  terminal = '';
  seq = 0;
  cols = 0;
  rows = 0;
  frame: string[] = [];
  history: string[] = [];
  historyFirst = 0;
  historyMore = false; // historyFirst: absolute index of history[0]
  holder: InputHolder | null = null;
  markers: Notifications['marker'][] = [];
  agent: string;
  private listeners = new Set<() => void>();
  constructor(agent: string) {
    this.agent = agent;
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  /** Scrollback then screen, without trailing blank rows. */
  text(): string[] {
    const all = [...this.history, ...this.frame];
    while (all.length && all[all.length - 1] === '') all.pop();
    return all;
  }
  /** True when the notice is the next one; false means a notice was missed and the view must reattach. */
  fits(seq: number): boolean {
    return seq === this.seq + 1;
  }

  setSnapshot(s: TerminalSnapshot): void {
    this.terminal = s.terminal;
    this.seq = s.seq;
    this.cols = s.cols;
    this.rows = s.rows;
    this.frame = s.lines.map((r) => r.text);
    this.history = s.history.lines.map((r) => r.text);
    this.historyFirst = s.history.first;
    this.historyMore = s.history.more;
    this.holder = s.holder;
    this.emit();
  }
  applyFrame(n: Notifications['frame']): void {
    if (n.full) this.frame = (n.lines ?? []).map((r) => r.text);
    for (const c of n.changed ?? []) this.frame[c.y] = c.row.text;
    this.seq = n.seq;
    this.emit();
  }
  applyResize(n: Notifications['resize']): void {
    this.cols = n.cols;
    this.rows = n.rows;
    this.seq = n.seq;
    this.frame = Array.from({ length: n.rows }, (_, i) => this.frame[i] ?? '');
    this.emit();
  }
  applyMarker(n: Notifications['marker']): void {
    this.markers.push(n);
    this.seq = n.seq;
    this.emit();
  }
  prependHistory(first: number, lines: string[], more: boolean): void {
    this.history = [...lines, ...this.history];
    this.historyFirst = first;
    this.historyMore = more;
    this.emit();
  }
  private emit(): void {
    for (const fn of this.listeners) fn();
  }
}
