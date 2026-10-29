import type { Meta, StoryObj } from '@storybook/react-vite';
import { TimeBar, TimeLeft } from '#src/ds/TimeBar.tsx';

const meta: Meta<typeof TimeBar> = { title: 'Patterns/Time bar', component: TimeBar };
export default meta;
type Story = StoryObj;

export const TimeLeftWithinLimit: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <TimeLeft text="40m left" />
      <TimeBar
        label="70% of the limit worked"
        shares={[
          { pct: 45, tone: 1 },
          { pct: 25, tone: 2 },
        ]}
      />
    </div>
  ),
};
export const PastTheLimit: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <TimeLeft text="12m over" over />
      <TimeBar
        label="Past the limit by 12%"
        shares={[
          { pct: 70, tone: 1 },
          { pct: 18, tone: 2 },
        ]}
        overPct={12}
      />
    </div>
  ),
};
