// The status bar (G12): 24 px along the bottom, each cell labelled: the daemon in a word, and the accounts' usage with its popup. The agents
// by state are the sidebar's, not repeated here.
import { useConnection } from '@handline/app-react';
import { UsagePopup } from './UsagePopup.tsx';
import { Chip, Icon } from './ui/index.ts';

const DAEMON = { open: 'daemon ok', connecting: 'connecting', reconnecting: 'reconnecting', closed: 'daemon down' } as const;

export function StatusBar() {
  const conn = useConnection();
  const up = conn === 'open';
  return (
    <footer className="statusbar flex h-6 items-center gap-3 border-t border-line bg-surface px-2 text-hint text-ink-2" aria-label="Status">
      <Chip variant="cell" title={`Daemon ${conn}`} role="img" aria-label={`daemon ${conn}`}>
        <Icon name={up ? 'online' : 'offline'} size={14} aria-hidden />
        <span className={up ? undefined : 'text-bad'}>{(DAEMON as Record<string, string>)[conn] ?? `daemon ${conn}`}</span>
      </Chip>
      <span className="status-end relative ml-auto">
        <UsagePopup />
      </span>
    </footer>
  );
}
