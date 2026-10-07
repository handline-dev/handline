// The keys of the app on every screen (G08), opened by `?`. The Inbox and Tasks pages keep their own `?` for their own keys.
import { Dialog } from './ui/index.ts';

const KEYS: [string, string][] = [
  ['⌘K', 'search everything'],
  ['⌘B', 'show or hide the sidebar'],
  ['⌘[  ⌘]', 'back, forward'],
  ['?', 'this list'],
  ['Esc', 'close what is open'],
];

export function KeysSheet({ onClose }: { onClose: () => void }) {
  return (
    <Dialog title="Keys" onClose={onClose}>
      <dl className="keys-list">
        {KEYS.map(([k, what]) => (
          <div key={k}>
            <dt>
              <kbd>{k}</kbd>
            </dt>
            <dd>{what}</dd>
          </div>
        ))}
      </dl>
    </Dialog>
  );
}
