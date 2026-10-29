// The sidebar's state marks: the agent's dot as the design system's StateMark.
import type { Dot } from '@handline/app-core';
import { StateMark, type StateName } from '#src/ds/StateMark.tsx';

export const MARKS: Record<Dot, { label: string }> = {
  running: { label: 'working' },
  waking: { label: 'starting in a moment' },
  waiting: { label: 'needs you' },
  blocked: { label: 'blocked' },
  idle: { label: 'idle' },
  parked: { label: 'waiting for a time' },
  none: { label: 'idle' },
};
export const DOT_ORDER: Dot[] = ['waiting', 'blocked', 'running', 'waking', 'idle', 'parked', 'none'];

const STATE: Record<Dot, StateName> = {
  running: 'working',
  waking: 'starting',
  waiting: 'needs',
  blocked: 'blocked',
  idle: 'idle',
  parked: 'waiting',
  none: 'idle',
};

/** An agent's state as the design system's mark: a glyph of its own shape per state, the hue only repeats it. */
export function Mark({ dot, title }: { dot: Dot; title?: string }) {
  return (
    <span className={`mark ${dot} inline-flex flex-none`}>
      <StateMark state={STATE[dot]} label={title ?? MARKS[dot].label} />
    </span>
  );
}

/** A small meter: a bar with its value as the tooltip (or `title`) and the aria value, never colour alone (the fill is also a length). `fill` is its colour; `hot` (from 80% by default) adds the amber and a hatch. */
export function Meter({
  pct,
  label,
  title,
  fill = 'bg-ink-2',
  hot = pct >= 80,
}: {
  pct: number;
  label: string;
  title?: string;
  fill?: string;
  hot?: boolean;
}) {
  return (
    <span
      className={`meter${hot ? ' hot' : ''} inline-block h-1.5 w-7 mx-[3px] overflow-hidden rounded-sm border border-ink-2 align-middle`}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      title={title ?? `${label} ${pct}%`}
    >
      <span
        className={`block h-full${hot ? ' bg-wait bg-[repeating-linear-gradient(45deg,transparent_0_2px,color-mix(in_srgb,var(--bg-0)_35%,transparent)_2px_4px)]' : ` ${fill}`}`}
        style={{ width: `${pct}%` }}
      />
    </span>
  );
}
