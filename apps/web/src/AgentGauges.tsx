// An agent's two gauges, one component for the sidebar row and the chat head: the context window FILLS as the session uses it (brain, blue), the
// prompt cache DRAINS as its minutes run out (timer, green). Each has its icon, so a bar is never read without knowing what it counts.
import { Icon } from './ui/index.ts';

/** The prompt cache lives an hour; a bar is full when it was just warmed. */
export const CACHE_MINUTES = 60;
const HOT_PCT = 80;

function Bar({ pct, fill, label }: { pct: number; fill: string; label: string }) {
  return (
    <span
      className="relative inline-block h-1.5 w-7 flex-none overflow-hidden rounded-chip border border-ink-2 bg-raised"
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
    >
      <span className={`block h-full ${fill}`} style={{ width: `${pct}%` }} />
    </span>
  );
}

/** `ctx` is whole percent used or null; `cacheMin` is minutes left, 0 or less once lapsed, null when none is on record. */
export function AgentGauges({ ctx, cacheMin }: { ctx: number | null; cacheMin: number | null }) {
  const hot = ctx !== null && ctx >= HOT_PCT;
  const lapsed = cacheMin !== null && cacheMin <= 0;
  return (
    <>
      {ctx !== null ? (
        <span className="ctx-gauge inline-flex flex-none items-center gap-1 text-ink-2" title={`Context window ${ctx}% full`}>
          <Icon name="context" size={12} aria-hidden />
          <Bar pct={Math.min(100, ctx)} fill={hot ? 'bg-wait' : 'bg-working'} label={`Context ${ctx}% full`} />
        </span>
      ) : null}
      {cacheMin !== null ? (
        <span
          className={`cache-gauge inline-flex flex-none items-center gap-1 text-ink-2${lapsed ? ' out' : ''}`}
          title={lapsed ? 'The prompt cache has lapsed' : `${cacheMin} min until the prompt cache lapses`}
        >
          <Icon name="cache" size={12} aria-hidden />
          <Bar
            pct={lapsed ? 0 : Math.min(100, Math.round((cacheMin / CACHE_MINUTES) * 100))}
            fill="bg-ok"
            label={lapsed ? 'Cache lapsed' : `Cache ${cacheMin} min left`}
          />
        </span>
      ) : null}
    </>
  );
}
