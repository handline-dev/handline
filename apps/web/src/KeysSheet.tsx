// The app's keys, opened by `?`. The Inbox and Tasks pages keep their own `?` for their own keys.
import { Dialog } from './ui/index.ts';

const KEYS: [string, string][] = [
  ['⌘K', 'search everything'],
  ['⌘B', 'show or hide the sidebar'],
  ['⌘[  ⌘]', 'back, forward'],
  ['?', 'this list'],
  ['Esc', 'close what is open'],
];

const COMPOSE: [string, string][] = [
  ['Enter', 'send the message'],
  ['Shift+Enter', 'a new line in the message'],
  ['↑ ↓', 'walk through what you sent before'],
  ['Ctrl+R', 'search what you sent'],
  ['/ Tab', 'the verbs of this agent'],
  ['#', 'refer to a task or a message'],
  ['Esc Esc', 'clear the box (the draft is kept)'],
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
      <h3 className="mt-2 text-label text-ink-3">In the message box</h3>
      <dl className="keys-list">
        {COMPOSE.map(([k, what]) => (
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
