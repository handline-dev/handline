// What the pane does with xterm that does not need a DOM: the stream events, the safety handlers for foreign bytes, the size rules.
// It works on the small surface `Terminal` and `@xterm/headless` share, so the same code runs in the browser and in Node tests.
import type { ByteEvent, ByteTerminalView, Notifications } from '@handline/client';

export interface Disposable {
  dispose(): void;
}
type CsiId = { prefix?: string; intermediates?: string; final: string };
export interface TermLike {
  write(data: Uint8Array | string, done?: () => void): void;
  reset(): void;
  resize(cols: number, rows: number): void;
  onData(fn: (data: string) => void): Disposable;
  parser: {
    registerCsiHandler(id: CsiId, fn: () => boolean): Disposable;
    registerOscHandler(ident: number, fn: (data: string) => boolean): Disposable;
    registerDcsHandler(id: { prefix?: string; intermediates?: string; final: string }, fn: () => boolean): Disposable;
  };
}
/** A terminal made for a host element, with the parts that only a browser can do. */
export interface PaneTerm {
  term: TermLike;
  /** The grid that fits the element, or null while it has no size. */
  propose(): { cols: number; rows: number } | null;
  /** Calls `fn` when the grid that fits the element becomes known or changes without the element changing size (the cells were just measured). */
  onFit?(fn: () => void): Disposable;
  setFontSize(px: number): void;
  /** A gutter tick on the cursor row with a tooltip: where the daemon typed. Never part of the stream. */
  mark(label: string): void;
  focus(): void;
  blur?(): void;
  dispose(): void;
}
export type CreateTerm = (host: HTMLElement) => PaneTerm;

export const MAX_UNWRITTEN = 1 << 20; // past this the stream is dropped and the screen taken again
const TITLE_MAX = 80;
const isControl = (code: number) => code < 0x20 || (code >= 0x7f && code <= 0x9f);

/**
 * The bytes are the agent's output. Queries (device attributes, cursor position, colours) are consumed here, because xterm would
 * answer through onData and the answers would reach the agent as keystrokes; tmux has already answered its program. OSC 52
 * (clipboard) does nothing. Titles are shown, capped and stripped of control characters, and never rename anything.
 */
export function guard(term: TermLike, onTitle: (title: string) => void): Disposable {
  const consume = () => true;
  const off = [
    term.parser.registerCsiHandler({ final: 'c' }, consume), // DA1
    term.parser.registerCsiHandler({ prefix: '>', final: 'c' }, consume), // DA2
    term.parser.registerCsiHandler({ prefix: '=', final: 'c' }, consume), // DA3
    term.parser.registerCsiHandler({ final: 'n' }, consume), // DSR, CPR
    term.parser.registerCsiHandler({ prefix: '?', final: 'n' }, consume),
    term.parser.registerCsiHandler({ intermediates: '$', final: 'p' }, consume), // DECRQM
    term.parser.registerCsiHandler({ prefix: '?', intermediates: '$', final: 'p' }, consume),
    term.parser.registerCsiHandler({ final: 't' }, consume), // window reports
    term.parser.registerDcsHandler({ intermediates: '$', final: 'q' }, consume), // DECRQSS
    term.parser.registerDcsHandler({ intermediates: '+', final: 'q' }, consume), // XTGETTCAP
    term.parser.registerOscHandler(52, consume),
    ...[4, 10, 11, 12].map((n) => term.parser.registerOscHandler(n, consume)), // colour queries and sets
    ...[0, 1, 2].map((n) =>
      term.parser.registerOscHandler(n, (data) => {
        onTitle(
          [...data]
            .filter((ch) => !isControl(ch.codePointAt(0)!))
            .join('')
            .slice(0, TITLE_MAX),
        );
        return true;
      }),
    ),
  ];
  return { dispose: () => off.forEach((d) => d.dispose()) };
}

const encode = new TextEncoder();
/** Scrollback goes in above the screen: history, then enough line feeds to push it off, then the snapshot from the top. */
export function repaintBytes(history: string[], rows: number, snapshot: Uint8Array): Uint8Array {
  if (!history.length) return snapshot;
  const lead = encode.encode(history.join('\r\n') + '\r\n' + '\r\n'.repeat(rows) + '\x1b[H\x1b[2J');
  const all = new Uint8Array(lead.length + snapshot.length);
  all.set(lead);
  all.set(snapshot, lead.length);
  return all;
}

/** Applies one stream event to the terminal. Returns the bytes handed to xterm, for the in-flight count. */
export function paint(term: TermLike, view: ByteTerminalView, e: ByteEvent, done: (n: number) => void): void {
  if (e.type === 'reset') {
    term.reset();
    term.resize(e.cols, e.rows);
    const data = repaintBytes(view.history, e.rows, e.data); // one write, so no frame shows between the reset and the screen
    term.write(data, () => done(data.length));
  } else if (e.type === 'write') term.write(e.data, () => done(e.data.length));
}

/** Mouse and focus reports xterm sends on its own once the stream turns those modes on; they are not typing. */
export const isReport = (data: string): boolean => data.startsWith('\x1b[') && 'MIO<'.includes(data[2] ?? '-');

export const DEFAULT_FONT = 13;
export const MIN_FONT = 9;
/** A viewer that does not hold the input follows the host's grid: the font is scaled to fit it, down to a floor. */
export function fontFor(widthPx: number, cols: number): number {
  if (!widthPx || !cols) return DEFAULT_FONT;
  return Math.max(MIN_FONT, Math.min(DEFAULT_FONT, Math.floor(widthPx / (cols * 0.62))));
}
export const clampGrid = (cols: number, rows: number) => ({
  cols: Math.min(400, Math.max(20, cols)),
  rows: Math.min(200, Math.max(5, rows)),
});

/** The grid to ask the host for, or null while the pane has no size or already shows this one. */
export function gridToSend(proposed: { cols: number; rows: number } | null, now: { cols: number; rows: number }) {
  if (!proposed || !Number.isFinite(proposed.cols) || !Number.isFinite(proposed.rows) || proposed.cols < 1 || proposed.rows < 1)
    return null;
  const g = clampGrid(proposed.cols, proposed.rows);
  return g.cols === now.cols && g.rows === now.rows ? null : g;
}

/** The one key the app keeps while a terminal has focus: it hands the keyboard back to the app (Ctrl+Shift+E). */
export const isEscapeChord = (e: Pick<KeyboardEvent, 'ctrlKey' | 'shiftKey' | 'metaKey' | 'altKey' | 'key'>): boolean =>
  e.ctrlKey && e.shiftKey && !e.metaKey && !e.altKey && e.key.toLowerCase() === 'e';

/** True when a key belongs to the terminal under focus, so the app's own shortcuts (palette, history, ?) must not act on it. */
export function terminalOwnsKey(e: Pick<KeyboardEvent, 'target' | 'ctrlKey' | 'shiftKey' | 'metaKey' | 'altKey' | 'key'>): boolean {
  const t = e.target;
  return t instanceof HTMLElement && !!t.closest('.xterm') && !isEscapeChord(e);
}

/** The bytes xterm sends for a named key, so a key chip takes the path typing takes. */
export const KEY_BYTES: Record<string, string> = {
  esc: '\x1b',
  'ctrl-c': '\x03',
  enter: '\r',
  up: '\x1b[A',
  down: '\x1b[B',
  tab: '\t',
  'shift-tab': '\x1b[Z',
};

/** The tooltip of a marker: what the daemon typed, or who did what to the input. */
export function markerLabel(m: Notifications['marker']): string {
  const who = m.actor?.name ?? 'someone';
  return m.kind === 'daemon_typed' ? `daemon typed: ${m.text ?? ''}` : `${who}: ${m.text ?? 'took a control action'}`;
}

const LOCK_RETRIES = 8;
const LOCK_RETRY_MS = 40;

/** Keys go one call at a time, in order: the daemon refuses a call that arrives while another types (gate terminal_lock), so keys typed
 *  meanwhile are joined into the next call and a refused call is tried again a few times. Any other refusal drops what was queued. */
export function keySender(send: (data: Uint8Array) => Promise<unknown>, refused: (e: Error) => void) {
  let queue: Uint8Array[] = [],
    busy = false;
  const pump = async () => {
    busy = true;
    while (queue.length) {
      const data = new Uint8Array(queue.reduce((n, q) => n + q.length, 0));
      queue.reduce((at, q) => (data.set(q, at), at + q.length), 0);
      queue = [];
      for (let i = 0; ; i++) {
        try {
          await send(data);
          break;
        } catch (e) {
          if ((e as { gate?: string }).gate === 'terminal_lock' && i < LOCK_RETRIES) await new Promise((r) => setTimeout(r, LOCK_RETRY_MS));
          else {
            refused(e as Error);
            queue = [];
            break;
          }
        }
      }
    }
    busy = false;
  };
  return (data: Uint8Array) => {
    queue.push(data);
    if (!busy) void pump();
  };
}
