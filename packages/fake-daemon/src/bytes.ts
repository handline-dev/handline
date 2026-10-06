// The `bytes` terminal format: the session's text as escape-coded output, the way a tmux pane would send it.
import type { TerminalBytesSnapshot } from '@handline/contract';
import { scrolledOff, tid, visible, type Session } from './session.ts';

const RESET = '\x1b[0m';
export const b64 = (s: string): string => Buffer.from(s, 'utf8').toString('base64');

/** One screen row with colour: the prompt marker cyan, the agent's bullet green. A row is never wider than the grid. */
export function coded(text: string, cols = Infinity): string {
  text = [...text].slice(0, cols).join('');
  if (text.startsWith('> ')) return `\x1b[36m>${RESET}${text.slice(1)}${RESET}`;
  if (text.startsWith('●')) return `\x1b[32m●${RESET}${text.slice(1)}${RESET}`;
  return `${text}${RESET}`;
}

/** The cursor rests at the end of the last row that has text. */
function cursorOf(s: Session): { x: number; y: number } {
  const rows = visible(s);
  let y = rows.length - 1;
  while (y > 0 && rows[y] === '') y--;
  return { x: Math.min(s.cols - 1, [...rows[y]!].length), y };
}
const place = (c: { x: number; y: number }) => `\x1b[${c.y + 1};${c.x + 1}H\x1b[?25h`;

export function snapshotOf(s: Session, history: number, holders: TerminalBytesSnapshot['holders']): Omit<TerminalBytesSnapshot, 'seq'> {
  const cursor = cursorOf(s);
  const off = scrolledOff(s),
    first = Math.max(0, off - history);
  return {
    terminal: tid(s),
    format: 'bytes',
    cols: s.cols,
    rows: s.rows,
    data: b64(
      visible(s)
        .map((r) => coded(r, s.cols))
        .join('\r\n') + place(cursor),
    ),
    alt_screen: false,
    cursor,
    cursor_visible: true,
    history: { first, lines: s.lines.slice(first, off).map((l) => coded(l, s.cols)), more: first > 0 },
    holder: holders[0]!,
    holders,
  };
}

/** The output that turns the old screen into the new one: each changed row rewritten in place. */
export function outputFor(s: Session, changed: number[]): string {
  const rows = visible(s);
  return b64(changed.map((y) => `\x1b[${y + 1};1H\x1b[2K${coded(rows[y]!, s.cols)}`).join('') + place(cursorOf(s)));
}

/** What the fake makes of raw input: printable runs are typed, CR is Enter, 0x03 and a lone ESC are keys; other escape sequences are dropped. */
export function typedParts(input: string): ({ text: string } | { key: string })[] {
  const parts: ({ text: string } | { key: string })[] = [];
  let run = '';
  const flush = () => {
    if (run) parts.push({ text: run });
    run = '';
  };
  for (let i = 0; i < input.length; i++) {
    const ch = input[i]!;
    const key = ch === '\r' ? 'enter' : ch === '\x03' ? 'ctrl-c' : ch === '\x1b' ? 'esc' : null;
    if (key === 'esc' && (input[i + 1] === '[' || input[i + 1] === 'O')) {
      i += 2; // a cursor key or similar: one final byte follows
      continue;
    }
    if (key) {
      flush();
      parts.push({ key });
    } else run += ch;
  }
  flush();
  return parts;
}
