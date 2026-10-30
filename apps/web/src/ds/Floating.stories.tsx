import type { Meta, StoryObj } from '@storybook/react-vite';
import { FoldVertical, Pin, Settings, Square, Trash2 } from 'lucide-react';
import { Button } from '#src/ds/Button.tsx';
import { Pop, PopItem, PopSeparator, Toast } from '#src/ds/Floating.tsx';
import { TimeBar } from '#src/ds/TimeBar.tsx';

const meta: Meta<typeof Pop> = { title: 'Parts/Menu, popover, toast', component: Pop };
export default meta;
type Story = StoryObj;

export const Menu: Story = {
  render: () => (
    <Pop>
      <PopItem icon={Pin}>Pin to top</PopItem>
      <PopItem icon={Settings}>Settings…</PopItem>
      <PopItem icon={Square}>Stop this turn</PopItem>
      <PopSeparator />
      <PopItem icon={Trash2} danger>
        Delete agent…
      </PopItem>
    </Pop>
  ),
};
export const Popover: Story = {
  render: () => (
    <Pop role="dialog" className="w-[260px] !p-3.5">
      <div className="flex justify-between">
        <span className="text-ink-2">Context</span>
        <b>26k / 200k</b>
      </div>
      <div className="my-2">
        <TimeBar label="26k of 200k" shares={[{ pct: 13, tone: 1 }]} />
      </div>
      <div className="flex justify-end">
        <Button size="s" icon={FoldVertical} label="Compact after this turn" />
      </div>
    </Pop>
  ),
};
export const ToastWithUndo: Story = { render: () => <Toast action="Undo">Merge PR #798, #799 · Merged</Toast> };
export const ToastPlain: Story = { render: () => <Toast>Copied</Toast> };
