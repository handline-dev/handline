// In the top bar when the daemon is not the build of the app around it (an app updated, a daemon left running): says so, and restarts the daemon onto the app's build.
import { useState } from 'react';
import { mismatch } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { shell, useShellBuild } from '#src/shell.ts';
import { Button } from '#src/ui/index.ts';

export function DaemonBuild() {
  const hello = useSelector((s) => s.client.hello);
  const build = useShellBuild();
  const [state, setState] = useState<{ busy: boolean; error: string }>({ busy: false, error: '' });
  const line = mismatch(hello?.daemon, build?.sha);
  if (!line) return null;
  const restart = async () => {
    setState({ busy: true, error: '' });
    const r = await shell('restartDaemon')!.restartDaemon!();
    setState({ busy: false, error: r.ok ? '' : r.message });
  };
  return (
    <div className="daemon-build flex min-w-0 max-w-[50%] flex-initial items-center gap-2 text-meta text-ink-2" role="status">
      <span className="size-1.5 flex-none rounded-full bg-wait" aria-hidden />
      <span className="truncate" title={state.error || line}>
        {state.error ? `Not restarted: ${state.error}` : line}
      </span>
      {build?.canRestart && shell('restartDaemon') ? (
        <Button label={state.busy ? 'Restarting…' : 'Restart daemon'} size="sm" disabled={state.busy} onClick={() => void restart()} />
      ) : null}
    </div>
  );
}
