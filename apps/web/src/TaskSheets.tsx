// The small sheets of the Tasks list: owner, priority, merge and the key sheet.
import { useState } from 'react';
import { Sheet } from './taskui.tsx';
import { Button, Kbd, NativeSelect } from './ui/index.ts';

/** The bridge's twelve keys (`TasksView.KEYS`) plus the web's `n`, for the key sheet. */
export const LIST_KEYS: [string, string][] = [
  ['Space', 'mark or unmark the row'],
  ['A', 'mark every row shown, or unmark them'],
  ['Enter', "open the task's detail"],
  ['d', 'discard, with a reason'],
  ['o', 'change the owner to another agent'],
  ['p', 'set the priority, 1 high to 3 low'],
  ['m', 'fold the marked tasks into the row under the cursor'],
  ['s / S', 'sort by the next column / reverse'],
  ['a', 'switch between Open and Closed tasks'],
  ['v', 'open the filters: agent and states'],
  ['t', 'switch between the list and the tree'],
  ['f', "show the next agent's tasks"],
  ['n', 'new task'],
  ['j k ↑ ↓', 'move the cursor'],
];

export function OwnerSheet({ agents, onCancel, onPick }: { agents: string[]; onCancel: () => void; onPick: (owner: string) => void }) {
  const [owner, setOwner] = useState('');
  return (
    <Sheet label="new owner (agent name):" submit="Change owner" onCancel={onCancel} onSubmit={() => owner && onPick(owner)}>
      <NativeSelect
        value={owner}
        aria-label="New owner"
        onChange={(e) => setOwner(e.target.value)}
      >
        <option value="">pick an agent</option>
        {agents.map((a) => (
          <option key={a}>{a}</option>
        ))}
      </NativeSelect>
    </Sheet>
  );
}

export function PrioritySheet({ onCancel, onPick }: { onCancel: () => void; onPick: (p: 1 | 2 | 3) => void }) {
  return (
    <Sheet label="priority (1 high, 2, 3 low):" submit="Set" onCancel={onCancel} onSubmit={onCancel}>
      {([1, 2, 3] as const).map((p) => (
        <Button key={p} variant="outline" label={`Priority ${p}`} className="max-sm:min-h-12 max-sm:flex-1" onClick={() => onPick(p)}>
          {p}
          {p === 1 ? ' high' : p === 3 ? ' low' : ''}
        </Button>
      ))}
    </Sheet>
  );
}

export function KeysSheet({ onClose }: { onClose: () => void }) {
  return (
    <Sheet label="Keys on this page" submit="Close" onCancel={onClose} onSubmit={onClose}>
      <dl className="keysheet m-0 grid grid-cols-2 gap-x-4 gap-y-1 basis-full">
        {LIST_KEYS.map(([k, v]) => (
          <div key={k} className="flex gap-2">
            <dt className="m-0 min-w-[56px]">
              <Kbd>{k}</Kbd>
            </dt>
            <dd className="m-0">{v}</dd>
          </div>
        ))}
      </dl>
    </Sheet>
  );
}
