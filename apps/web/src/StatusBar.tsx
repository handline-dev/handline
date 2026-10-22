// The status bar (G12): 24 px along the bottom, each cell labelled: the daemon, the work waiting on the person or blocked, and the accounts'
// usage with its popup. The agents by state are the sidebar's, not repeated here.
import { useConnection } from '@handline/app-react';
import { machineCells } from '@handline/app-core';
import { useStatus, useTasks } from '@handline/app-data';
import { useMyTurn } from '#src/MyTurn.tsx';
import { UsageLink } from '#src/UsageLink.tsx';
import { Chip, Icon } from '#src/ui/index.ts';

const DAEMON = { open: 'daemon ok', connecting: 'connecting', reconnecting: 'reconnecting', closed: 'daemon down' } as const;

export function StatusBar() {
  const conn = useConnection();
  const up = conn === 'open';
  const waiting = useMyTurn().cards.length;
  const blocked = (useTasks({}).data?.items ?? []).filter((t) => t.state === 'blocked').length;
  const cells = machineCells(useStatus().data?.resources);
  return (
    <footer className="statusbar flex h-6 items-center gap-3 border-t border-line bg-surface px-2 text-hint text-ink-2" aria-label="Status">
      <Chip variant="cell" title={`Daemon ${conn}`} role="img" aria-label={`daemon ${conn}`}>
        <Icon name={up ? 'online' : 'offline'} size={14} aria-hidden />
        <span className={up ? undefined : 'text-bad'}>{(DAEMON as Record<string, string>)[conn] ?? `daemon ${conn}`}</span>
      </Chip>
      <Chip variant="cell" as="a" href="#/my-turn" title="Items waiting on you">
        <span className={waiting ? 'text-wait' : undefined}>{waiting} waiting on you</span>
      </Chip>
      <Chip variant="cell" as="a" href="#/tasks" title="Tasks that are blocked">
        <span className={blocked ? 'text-wait' : undefined}>{blocked} blocked</span>
      </Chip>
      {cells ? (
        <Chip variant="cell" as="a" href="#/admin" title="What the machine and the agents use">
          {cells.map((c, i) => (
            <span key={i} className={c.over ? 'text-bad' : undefined} data-over={c.over || undefined}>
              {i ? ' · ' : ''}
              {c.text}
            </span>
          ))}
        </Chip>
      ) : null}
      <span className="status-end relative ml-auto">
        <UsageLink />
      </span>
    </footer>
  );
}
