// An agent's two gauges, one component for the sidebar row and the chat head: the context window FILLS as the session uses it (database, blue), the
// prompt cache DRAINS as its minutes run out (timer, green). Each has its icon, so a bar is never read without knowing what it counts.
import type { ReactNode } from 'react';
import { Icon, MenuButton, STRIP_ITEM } from '#src/ui/index.ts';
import { Meter } from '#src/RailIcons.tsx';

/** The prompt cache lives an hour; a bar is full when it was just warmed. */
export const CACHE_MINUTES = 60;

/** A gauge that opens its details when `info` is given (the chat footer); a plain one otherwise (the sidebar row). */
function Gauge({ name, info, children }: { name: string; info?: ReactNode; children: ReactNode }) {
  if (!info) return <span className="inline-flex flex-none items-center text-ink-2">{children}</span>;
  return (
    <MenuButton
      className={`${STRIP_ITEM} flex-none gap-0 text-hint text-ink-2`}
      label={`${name} details`}
      aria-label={`${name} details`}
      name={name}
      align="start"
      trigger={children}
    >
      <div className="min-w-48 px-2 pb-1.5 pt-0.5 text-hint text-ink-2">{info}</div>
    </MenuButton>
  );
}

/** One line of a gauge's details: what it is, then its value. */
export const GaugeLine = ({ what, children }: { what: string; children: ReactNode }) => (
  <div className="flex justify-between gap-4 py-0.5">
    <span className="text-ink-3">{what}</span>
    <span className="whitespace-nowrap text-ink">{children}</span>
  </div>
);

/** `ctx` is whole percent used or null; `cacheMin` is minutes left (0 or less once lapsed), null when none is on record; the `*Text` props say each in words. */
export function AgentGauges({
  ctx,
  cacheMin,
  ctxText,
  cacheText,
  ctxInfo,
  cacheInfo,
}: {
  ctx: number | null;
  cacheMin: number | null;
  ctxText?: string;
  cacheText?: string;
  ctxInfo?: ReactNode;
  cacheInfo?: ReactNode;
}) {
  const lapsed = cacheMin !== null && cacheMin <= 0;
  return (
    <>
      {ctx !== null ? (
        <Gauge name="Context window" info={ctxInfo}>
          <Icon name="context" size={12} aria-hidden />
          <Meter pct={Math.min(100, ctx)} label="Context window" title={`Context window ${ctx}% full`} fill="bg-working" />
          {ctxText ? <span className="ml-1 whitespace-nowrap">{ctxText}</span> : null}
        </Gauge>
      ) : null}
      {cacheMin !== null ? (
        <Gauge name="Prompt cache" info={cacheInfo}>
          <Icon name="cache" size={12} aria-hidden />
          <Meter
            pct={lapsed ? 0 : Math.min(100, Math.round((cacheMin / CACHE_MINUTES) * 100))}
            label="Prompt cache"
            title={lapsed ? 'The prompt cache has lapsed' : `${cacheMin} min until the prompt cache lapses`}
            fill="bg-ok"
            hot={false}
          />
          {cacheText ? <span className="ml-1 whitespace-nowrap">{cacheText}</span> : null}
        </Gauge>
      ) : null}
    </>
  );
}
