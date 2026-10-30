// When the daemon is not the build of the app around it (an app updated, a daemon left running), the app moves the daemon itself, once per pair of builds; only a failure shows, as a closable notice with Try again.
import { useEffect, useRef } from 'react';
import { mismatch } from '@handline/app-core';
import { useSelector, useStore } from '@handline/app-react';
import { shell, useShellBuild } from '#src/shell.ts';

const KEY = 'daemon-restart';

export function DaemonBuild() {
  const hello = useSelector((s) => s.client.hello);
  const build = useShellBuild();
  const store = useStore();
  const tried = useRef(new Set<string>());
  const pair = `${hello?.daemon}/${build?.sha}`;
  const apart = !!mismatch(hello?.daemon, build?.sha);
  const movable = !!build?.canRestart && !!shell('restartDaemon');
  const restart = async () => {
    const r = await shell('restartDaemon')!.restartDaemon!().catch((e: unknown) => ({
      ok: false as const,
      message: e instanceof Error ? e.message : String(e),
    }));
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
  useEffect(() => {
    if (!apart) store.notices.drop(KEY); // the daemon is on the app's build now
    else if (movable && !tried.current.has(pair)) {
      tried.current.add(pair);
      void restart();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apart, movable, pair]);
  return null;
}
