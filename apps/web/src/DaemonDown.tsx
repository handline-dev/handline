// Shown above the page while the daemon is not reachable: says so, and what to do about it.
import { useConnection } from '@handline/app-react';
import { Icon } from './ui/index.ts';

export function DaemonDown() {
  const conn = useConnection();
  if (conn !== 'closed' && conn !== 'reconnecting') return null;
  return (
    <section className="daemon-down m-2 flex items-start gap-2.5 rounded-card border-2 border-bad bg-panel px-3 py-2.5" role="alert" aria-label="Daemon not running">
      <Icon name="offline" size={18} aria-hidden />
      <div>
        <strong>Daemon not running</strong>
        <p className="muted m-0 mt-0.5">
          {conn === 'reconnecting' ? 'Reconnecting… ' : ''}What you see is the last it told this window. To start it, run{' '}
          <code>hl service install</code> in a terminal on the host; this page reconnects by itself.
        </p>
      </div>
    </section>
  );
}
