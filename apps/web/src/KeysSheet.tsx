// The app's keys, then the keys each mounted screen registered (screenKeys), opened by `?`. The Inbox and Tasks pages keep their own `?`.
import { useRegisteredKeys } from './screenKeys.ts';
import { Dialog, Kbd } from './ui/index.ts';

const KEYS: [string, string][] = [
  ['⌘K', 'search everything'],
  ['⌘B', 'show or hide the sidebar'],
  ['⌘[  ⌘]', 'back, forward'],
  ['?', 'this list'],
  ['Esc', 'close what is open'],
];

export function KeysSheet({ onClose }: { onClose: () => void }) {
  const registered = useRegisteredKeys();
  const groups = [...new Map(registered.map((g) => [g.rows, g])).values()]; // one per table, since each pane registers the same rows
  return (
    <Dialog title="Keys" onClose={onClose}>
      <dl className="keys-list m-0 grid gap-1.5">
        {KEYS.map(([k, what]) => (
          <div key={k} className="flex items-baseline gap-3">
            <dt className="min-w-20">
              <Kbd>{k}</Kbd>
            </dt>
            <dd className="m-0 text-ink-2">{what}</dd>
          </div>
        ))}
      </dl>
      {groups.map((g) => (
        <section key={g.title}>
          <h3 className="mt-2 text-label text-ink-3">{g.title}</h3>
          <dl className="keys-list m-0 grid gap-1.5">
            {g.rows.map(([k, what]) => (
              <div key={k} className="flex items-baseline gap-3">
                <dt className="min-w-20">
                  <Kbd>{k}</Kbd>
                </dt>
                <dd className="m-0 text-ink-2">{what}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </Dialog>
  );
}
