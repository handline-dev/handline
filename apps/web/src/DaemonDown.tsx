// The one line that says the daemon is not reachable, with Retry. Everything else keeps quiet while it shows: no skeletons, no
// "Connecting…", no "Pick an agent" (they would say the same three times).
import { useEffect, useState } from 'react';
import { useConnection } from '@handline/app-react';
import { Button, Icon } from './ui/index.ts';

/** Whether the daemon cannot be reached: lost, or a first connect that has taken over a second (a quick one just shows the skeletons). */
export const useOffline = (): boolean => {
  const conn = useConnection();
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    setSlow(false);
    if (conn !== 'connecting') return;
    const t = setTimeout(() => setSlow(true), 1000);
    return () => clearTimeout(t);
  }, [conn]);
  return conn === 'closed' || conn === 'reconnecting' || (conn === 'connecting' && slow);
};

export function DaemonDown() {
  const conn = useConnection();
  if (!useOffline()) return null;
  return (
    <section
      className="daemon-down flex flex-none items-center gap-2 border-t border-bad bg-panel px-3 py-1.5"
      role="alert"
      aria-label="Daemon not running"
    >
      <Icon name="offline" size={14} aria-hidden />
      <span className="min-w-0 flex-1">
        {conn === 'closed' ? 'Daemon not running. ' : 'Reconnecting to the daemon… '}
        <span className="muted">
          What you see is the last it told this window; to start it run <code>hl service install</code> on the host.
        </span>
      </span>
      <Button variant="outline" size="sm" label="Retry" onClick={() => location.reload()} />
    </section>
  );
}
