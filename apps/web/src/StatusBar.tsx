// The status bar (G12): 24 px along the bottom, each cell an icon and a number with its words as the tooltip: the daemon, the agents by
// state, and the accounts' usage with its popup.
import { dotOf, type Dot } from '@handline/app-core';
import { useConnection } from '@handline/app-react';
import { useAgents } from '@handline/app-data';
import { DOT_ORDER, Mark, MARKS } from './RailIcons.tsx';
import { UsagePopup } from './UsagePopup.tsx';
import { Icon } from './ui/index.ts';

export function StatusBar() {
  const conn = useConnection();
  const agents = useAgents().data?.agents ?? [];
  const counts = new Map<Dot, number>();
  for (const a of agents) counts.set(dotOf(a), (counts.get(dotOf(a)) ?? 0) + 1);
  const up = conn === 'open';
  return (
    <footer className="statusbar flex h-6 items-center gap-3 border-t border-line bg-surface px-2 text-hint text-ink-2" aria-label="Status">
      <span className="status-cell inline-flex items-center gap-1" title={`Daemon ${conn}`} role="img" aria-label={`daemon ${conn}`}>
        <Icon name={up ? 'online' : 'offline'} size={14} aria-hidden />
      </span>
      <span className="status-cell inline-flex items-center gap-1" title={`${agents.length} agents`}>
        <Icon name="agent" size={14} aria-hidden />
        {agents.length}
      </span>
      {DOT_ORDER.filter((d) => counts.get(d)).map((d) => (
        <span key={d} className="status-cell inline-flex items-center gap-1" title={`${counts.get(d)} ${MARKS[d].label}`}>
          <Mark dot={d} />
          {counts.get(d)}
        </span>
      ))}
      <span className="status-end relative ml-auto">
        <UsagePopup />
      </span>
    </footer>
  );
}
