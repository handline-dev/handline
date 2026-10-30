import type { Meta, StoryObj } from '@storybook/react-vite';
import { ListOrdered, ShieldQuestion } from 'lucide-react';
import { AgentRow } from '#src/ds/AgentRow.tsx';
import { Chip } from '#src/ds/Chip.tsx';
import { Id } from '#src/ds/Link.tsx';

const meta: Meta<typeof AgentRow> = { title: 'Patterns/Agent row', component: AgentRow };
export default meta;
type Story = StoryObj;

const side = (S: () => React.JSX.Element) => (
  <div className="w-64 rounded-md bg-surface p-2">
    <S />
  </div>
);

export const Working: Story = {
  args: {
    state: 'working',
    name: 'handline',
    selected: true,
    time: '1s',
    chip: <Chip icon={ListOrdered}>3</Chip>,
    line: (
      <>
        Reading K7016 · <Id>K7016</Id>
      </>
    ),
  },
  decorators: [side],
};
export const AsksPermission: Story = {
  args: {
    state: 'needs',
    name: 'architect',
    time: '2m',
    hot: true,
    chip: (
      <Chip kind="you" icon={ShieldQuestion}>
        permission
      </Chip>
    ),
    line: 'Run go test ./... in daemon',
  },
  decorators: [side],
};
export const UnreadWords: Story = {
  args: {
    state: 'idle',
    name: 'daemon-core',
    time: '1h',
    hot: true,
    chip: <Chip kind="unread">2</Chip>,
    line: 'PR #801 is up for the build stamp',
  },
  decorators: [side],
};
export const Blocked: Story = { args: { state: 'blocked', name: 'pc-tasks', time: '14m', line: 'waits on K7020' }, decorators: [side] };
