// In the top bar when the daemon is not the build of the app around it (an app updated, a daemon left running): says so, and restarts the daemon onto the app's build.
import { useState } from 'react';
import { mismatch } from '@handline/app-core';
import { useSelector, useStore } from '@handline/app-react';
import { shell, useShellBuild } from '#src/shell.ts';
import { Button, Icon } from '#src/ui/index.ts';

const KEY = 'daemon-restart';

export function DaemonBuild() {
  const hello = useSelector((s) => s.client.hello);
  const build = useShellBuild();
  const store = useStore();
  const [state, setState] = useState<{ busy: boolean; error: string }>({ busy: false, error: '' });
  const line = mismatch(hello?.daemon, build?.sha);
  if (!line) return null;
  const restart = async () => {
    setState({ busy: true, error: '' });
    const r = await shell('restartDaemon')!.restartDaemon!().catch((e: unknown) => ({
      ok: false as const,
      message: e instanceof Error ? e.message : String(e),
    }));
    setState({ busy: false, error: r.ok ? '' : r.message });
    // The whole reason is for the bottom-right notice: the top bar keeps to one line.
    if (r.ok) store.notices.drop(KEY);
    else
      store.notices.keep(
        KEY,
        'fail',
        'Daemon not restarted',
        { step: r.message, actions: [{ label: 'Try again', run: () => void restart(), primary: true }] },
        true,
      );
  };
  return (
    <div
      className="daemon-build flex min-w-0 max-w-[50%] flex-initial items-center gap-2 text-meta text-ink-2"
      role="status"
      title={state.error ? 'Not restarted' : line}
    >
      {state.error ? (
        <Icon name="warn" size={14} className="flex-none text-bad" aria-hidden />
      ) : (
        <span className="size-1.5 flex-none rounded-full bg-wait" aria-hidden />
      )}
      <span className={state.error ? 'truncate' : 'truncate max-[1100px]:sr-only'}>{state.error ? 'Not restarted' : line}</span>
      {build?.canRestart && shell('restartDaemon') ? (
        <Button label={state.busy ? 'Restarting…' : 'Restart daemon'} size="sm" disabled={state.busy} onClick={() => void restart()} />
      ) : null}
    </div>
  );
}
