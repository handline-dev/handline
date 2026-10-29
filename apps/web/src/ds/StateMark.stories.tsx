import type { Meta, StoryObj } from '@storybook/react-vite';
import { StateMark, type StateName } from '#src/ds/StateMark.tsx';

const meta: Meta<typeof StateMark> = { title: 'Foundations/State', component: StateMark };
export default meta;
type Story = StoryObj;

const ALL: StateName[] = ['working', 'needs', 'blocked', 'idle', 'waiting', 'starting', 'done', 'dropped', 'draft'];

export const EveryState: Story = {
  render: () => (
    <div className="flex flex-col gap-1.5">
      {ALL.map((s) => (
        <StateMark key={s} state={s} word />
      ))}
    </div>
  ),
};
export const GlyphOnly: Story = { args: { state: 'needs' } };
