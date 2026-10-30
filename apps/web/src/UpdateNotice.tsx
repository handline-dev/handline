// The one update notice, in the bottom-right stack: preparing (spinner, step, bar), ready (Restart now, Later), later (only the bell row keeps Restart
// now) and failed (the reason, Try again, Details). The desktop shell says where the update is; the buttons press its calls.
import { useEffect } from 'react';
import { useStore } from '@handline/app-react';
import { shell } from '#src/shell.ts';

const KEY = 'update';

export function UpdateNotice() {
  const store = useStore();
  useEffect(() => {
    const api = shell('onUpdate');
    if (!api?.onUpdate) return;
    const press = (what: 'restart' | 'later' | 'retry' | 'details') => void shell('updateAct')?.updateAct?.(what);
    const restart = { label: 'Restart now', run: () => press('restart'), primary: true };
    let phase = '';
    return api.onUpdate((v) => {
      // A phase brings the toast back; progress inside one does not, so a closed notice stays closed.
      const show = v.phase !== phase;
      phase = v.phase;
      switch (v.phase) {
        case 'idle':
          store.notices.drop(KEY);
          break;
        case 'preparing':
          store.notices.keep(KEY, 'info', 'Updating Handline…', { busy: true, step: v.step, fraction: v.fraction }, show);
          break;
        case 'ready':
          store.notices.keep(
            KEY,
            'need',
            'Handline is ready to restart',
            {
              mark: 'restart',
              step: 'Agents keep working; open chats reconnect.',
              actions: [restart, { label: 'Later', run: () => press('later') }],
            },
            show,
          );
          break;
        case 'later':
          store.notices.keep(
            KEY,
            'need',
            'Handline is ready to restart',
            { mark: 'restart', step: 'Agents keep working; open chats reconnect.', actions: [restart] },
            false,
          );
          break;
        case 'failed':
          store.notices.keep(
            KEY,
            'fail',
            'Handline was not updated',
            {
              step: v.reason,
              actions: [
                { label: 'Try again', run: () => press('retry'), primary: true },
                { label: 'Details', run: () => press('details'), stay: true },
              ],
            },
            show,
          );
      }
    });
  }, [store]);
  return null;
}
