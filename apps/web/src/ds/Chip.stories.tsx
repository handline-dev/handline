import type { Meta, StoryObj } from '@storybook/react-vite';
import { HardDrive, ListOrdered, MessageCircleQuestion, ShieldQuestion, SquareCheck, Terminal, Users } from 'lucide-react';
import { Chip } from '#src/ds/Chip.tsx';

const meta: Meta<typeof Chip> = { title: 'Parts/Chip', component: Chip };
export default meta;
type Story = StoryObj;

export const Source: Story = {
  render: () => (
    <div className="flex gap-3">
      <Chip icon={SquareCheck}>Task</Chip>
      <Chip icon={Terminal}>Session</Chip>
      <Chip icon={Users}>HR</Chip>
      <Chip icon={HardDrive}>Machine</Chip>
    </div>
  ),
};
export const NeedsYou: Story = {
  render: () => (
    <div className="flex gap-3">
      <Chip kind="you" icon={ShieldQuestion}>
        permission
      </Chip>
      <Chip kind="you" icon={MessageCircleQuestion}>
        question
      </Chip>
    </div>
  ),
};
export const Unread: Story = { args: { kind: 'unread', children: '2' } };
export const Count: Story = { args: { kind: 'count', children: '3' } };
export const QueuedAfterNow: Story = { args: { icon: ListOrdered, children: '3' } };
