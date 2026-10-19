// The status bar (G12): 24 px along the bottom, each cell labelled: the daemon, the work waiting on the person or blocked, and the accounts'
// usage with its popup. The agents by state are the sidebar's, not repeated here.
import { useConnection } from '@handline/app-react';
import { useTasks } from '@handline/app-data';
import { useMyTurn } from './MyTurn.tsx';
import { UsagePopup } from './UsagePopup.tsx';
import { Chip, Icon } from './ui/index.ts';

const DAEMON = { open: 'daemon ok', connecting: 'connecting', reconnecting: 'reconnecting', closed: 'daemon down' } as const;

export function StatusBar() {
  const conn = useConnection();
  const up = conn === 'open';
  const waiting = useMyTurn().cards.length;
  const blocked = (useTasks({}).data?.items ?? []).filter((t) => t.state === 'blocked').length;
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
      <span className="status-end relative ml-auto">
        <UsagePopup />
      </span>
    </footer>
  );
}
