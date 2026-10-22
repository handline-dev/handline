// An agent's two gauges, one component for the sidebar row and the chat head: the context window FILLS as the session uses it (database, blue), the
// prompt cache DRAINS as its minutes run out (timer, green). Each has its icon, so a bar is never read without knowing what it counts.
import { Icon } from '#src/ui/index.ts';
import { Meter } from '#src/RailIcons.tsx';

/** The prompt cache lives an hour; a bar is full when it was just warmed. */
export const CACHE_MINUTES = 60;

/** `ctxText` (`120k / 200k`) and `cacheText` (`56m`) say the same in words. `ctx` is whole percent used or null; `cacheMin` is minutes left, 0 or less once lapsed, null when none is on record. */
export function AgentGauges({ ctx, cacheMin, ctxText, cacheText }: { ctx: number | null; cacheMin: number | null; ctxText?: string; cacheText?: string }) {
  const lapsed = cacheMin !== null && cacheMin <= 0;
  return (
    <>
      {ctx !== null ? (
        <span className="inline-flex flex-none items-center text-ink-2">
          <Icon name="context" size={12} aria-hidden />
          <Meter pct={Math.min(100, ctx)} label="Context window" title={`Context window ${ctx}% full`} fill="bg-working" />
          {ctxText ? <span className="ml-1 whitespace-nowrap">{ctxText}</span> : null}
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
          {cacheText ? <span className="ml-1 whitespace-nowrap">{cacheText}</span> : null}
        </span>
      ) : null}
    </>
  );
}
