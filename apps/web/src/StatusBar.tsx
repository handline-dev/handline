// The status bar (G12): 24 px along the bottom, each cell labelled: the daemon, the tasks that are blocked, and the accounts'
// usage with its popup. The agents by state are the sidebar's, not repeated here.
import { useConnection } from '@handline/app-react';
import { awayLine, useAway, useTasks } from '@handline/app-data';
import { NoticeBell } from '#src/Notices.tsx';
import { ResourceArea } from '#src/ResourceArea.tsx';
import { UsageLink } from '#src/UsageLink.tsx';
import { Chip, Icon } from '#src/ui/index.ts';

const DAEMON = { open: 'daemon ok', connecting: 'connecting', reconnecting: 'reconnecting', closed: 'daemon down' } as const;

export function StatusBar() {
  const conn = useConnection();
  const up = conn === 'open';
  const blocked = (useTasks({}).data?.items ?? []).filter((t) => t.state === 'blocked').length;
  const { away, dismiss } = useAway();
  return (
    <footer className="statusbar flex h-6 items-center gap-3 border-t border-line bg-surface px-2 text-hint text-ink-2" aria-label="Status">
      <Chip variant="cell" title={`Daemon ${conn}`} role="img" aria-label={`daemon ${conn}`}>
        <Icon name={up ? 'online' : 'offline'} size={14} aria-hidden />
        <span className={up ? undefined : 'text-bad'}>{(DAEMON as Record<string, string>)[conn] ?? `daemon ${conn}`}</span>
      </Chip>
      <Chip variant="cell" as="a" href="#/tasks" title="Tasks that are blocked">
        <span className={blocked ? 'text-wait' : undefined}>{blocked} blocked</span>
      </Chip>
      {away ? (
        <Chip variant="cell" as="button" title="Hide" className="cursor-pointer border-0 bg-transparent p-0 text-wait" onClick={dismiss}>
          {awayLine(away)} ✕
        </Chip>
      ) : null}
      <span className="status-end relative ml-auto flex items-center gap-3">
        <ResourceArea />
        <UsageLink />
        <NoticeBell />
      </span>
    </footer>
  );
}
