// An agent's two gauges, one component for the sidebar row and the chat head: the context window FILLS as the session uses it (database, blue), the
// prompt cache DRAINS as its minutes run out (timer, green). Each has its icon, so a bar is never read without knowing what it counts.
import { Icon } from './ui/index.ts';
import { Meter } from './RailIcons.tsx';

/** The prompt cache lives an hour; a bar is full when it was just warmed. */
export const CACHE_MINUTES = 60;

/** `ctx` is whole percent used or null; `cacheMin` is minutes left, 0 or less once lapsed, null when none is on record. */
export function AgentGauges({ ctx, cacheMin }: { ctx: number | null; cacheMin: number | null }) {
  const lapsed = cacheMin !== null && cacheMin <= 0;
  return (
    <>
      {ctx !== null ? (
        <span className="inline-flex flex-none items-center text-ink-2">
          <Icon name="context" size={12} aria-hidden />
          <Meter pct={Math.min(100, ctx)} label="Context window" title={`Context window ${ctx}% full`} fill="bg-working" />
        </span>
      ) : null}
      {cacheMin !== null ? (
        <span className="inline-flex flex-none items-center text-ink-2">
          <Icon name="cache" size={12} aria-hidden />
          <Meter
            pct={lapsed ? 0 : Math.min(100, Math.round((cacheMin / CACHE_MINUTES) * 100))}
            label="Prompt cache"
            title={lapsed ? 'The prompt cache has lapsed' : `${cacheMin} min until the prompt cache lapses`}
            fill="bg-ok"
            hot={false}
          />
        </span>
      ) : null}
    </>
  );
}
