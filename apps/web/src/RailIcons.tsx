// The sidebar's state marks: the agent's dot as the shared State glyph.
import type { Dot } from '@handline/app-core';
import { State, type StateKind } from './ui/index.ts';

export const MARKS: Record<Dot, { label: string }> = {
  running: { label: 'working' },
  waking: { label: 'waking: starts at the next tick' },
  waiting: { label: 'a person must answer' },
  blocked: { label: 'blocked or stalled' },
  idle: { label: 'idle' },
  parked: { label: 'parked: waits for a time' },
  none: { label: 'no terminal' },
};
export const DOT_ORDER: Dot[] = ['waiting', 'blocked', 'running', 'waking', 'idle', 'parked', 'none'];

const KIND: Record<Dot, StateKind> = {
  running: 'working',
  waking: 'booting',
  waiting: 'needs',
  blocked: 'blocked',
  idle: 'idle',
  parked: 'parked',
  none: 'noTerminal',
};

/** An agent's state as the shared glyph (ui/State): its own shape per state, the hue only repeats it. */
export function Mark({ dot, title }: { dot: Dot; title?: string }) {
  return (
    <span className={`mark ${dot}`}>
      <State kind={KIND[dot]} label={title ?? MARKS[dot].label} size={14} />
    </span>
  );
}

/** A small meter: a bar with its value as the tooltip and the aria value, never colour alone (the fill is also a length). */
export function Meter({ pct, label }: { pct: number; label: string }) {
  return (
    <span
      className={`meter${pct >= 80 ? ' hot' : ''}`}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      title={`${label} ${pct}%`}
    >
      <span style={{ width: `${pct}%` }} />
    </span>
  );
}
