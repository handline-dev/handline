// The agents by use: a name, then CPU and memory each as a number over a light bar (its share of the biggest agent's), and Stop when the row can be stopped.
type Row = { agent: string; cpu: string; memory: string; cpuPct: number; rss: number };

/** A figure over a tinted bar that fills the cell to `pct` percent. */
function Cell({ text, pct, head }: { text: string; pct: number; head?: string }) {
  return (
    <span
      className="relative flex h-6 min-w-0 items-center justify-end overflow-hidden rounded-sm px-2 text-right tabular-nums"
      title={head}
    >
      <span
        className="absolute inset-y-0 left-0 bg-[color-mix(in_srgb,var(--s-ok)_22%,transparent)]"
        style={{ width: `${Math.min(100, Math.max(2, pct))}%` }}
        aria-hidden
      />
      <span className="relative">{text}</span>
    </span>
  );
}

/** Part of the biggest figure in its column, so the bars compare the agents with each other. */
const share = (part: number, most: number) => (most > 0 ? (part / most) * 100 : 0);

export function AgentUse({
  rows,
  stop,
}: {
  rows: Row[];
  /** Opens the stop sheet for an agent; absent where the page cannot stop one. */
  stop?: (agent: string) => void;
}) {
  const topCpu = Math.max(0, ...rows.map((x) => x.cpuPct));
  const topRss = Math.max(0, ...rows.map((x) => x.rss));
  const cols = stop ? 'grid-cols-[minmax(0,1fr)_5.5rem_5.5rem_2.5rem]' : 'grid-cols-[minmax(0,1fr)_5.5rem_5.5rem]';
  return (
    <div className="agent-use text-md" role="table" aria-label="Agents by use">
      <div role="row" className={`grid ${cols} items-center gap-x-1 pb-1 text-sm text-ink-3`}>
        <span role="columnheader">Agent</span>
        <span role="columnheader" className="px-2 text-right">
          CPU
        </span>
        <span role="columnheader" className="px-2 text-right">
          Memory
        </span>
        {stop ? <span role="columnheader" /> : null}
      </div>
      {rows.map((x) => (
        <div key={x.agent} role="row" data-agent={x.agent} className={`grid ${cols} items-center gap-x-1 py-0.5`}>
          <a role="cell" className="truncate text-ink" href={`#/agents/${encodeURIComponent(x.agent)}`}>
            {x.agent}
          </a>
          <Cell text={x.cpu} pct={share(x.cpuPct, topCpu)} />
          <Cell text={x.memory} pct={share(x.rss, topRss)} />
          {stop ? (
            <button
              type="button"
              className="tint cursor-pointer rounded-sm border-0 bg-transparent px-1 py-0.5 text-sm text-ink-2"
              aria-label={`Stop ${x.agent}`}
              onClick={() => stop(x.agent)}
            >
              Stop
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}
