import type { Meta, StoryObj } from '@storybook/react-vite';
import { ShieldCheck, SquareCheck, Terminal, Users } from 'lucide-react';
import { Chip } from '#src/ds/Chip.tsx';
import { Id } from '#src/ds/Link.tsx';
import { EmptyList, ListRow, SectionHead } from '#src/ds/ListRow.tsx';

const meta: Meta<typeof ListRow> = { title: 'Patterns/List', component: ListRow };
export default meta;
type Story = StoryObj;

export const Rows: Story = {
  render: () => (
    <div className="max-w-[440px]">
      <SectionHead count={3} icon={<ShieldCheck width={14} height={14} strokeWidth={1.75} aria-hidden />}>
        Allow
      </SectionHead>
      <ListRow title="Run a command" chip={<Chip icon={Terminal}>Session</Chip>} time="2m" />
      <ListRow selected title="Merge PR #798, #799" chip={<Chip icon={SquareCheck}>Task</Chip>} time="40m" />
      <ListRow title="Team changes: 5 new agents under ux" chip={<Chip icon={Users}>HR</Chip>} time="5m" />
      <SectionHead>Today</SectionHead>
      <ListRow done lead={<Id>T6950</Id>} title="데몬 빌드 표시를 좁은 화면에서 줄여줘" time="3h" />
    </div>
  ),
};
export const Selected: Story = {
  args: { selected: true, title: 'Merge PR #798, #799', time: '40m' },
  decorators: [
    (S) => (
      <div className="max-w-[440px]">
        <S />
      </div>
    ),
  ],
};
export const Done: Story = {
  args: { done: true, title: 'Finished work reads quieter', time: '3h' },
  decorators: [
    (S) => (
      <div className="max-w-[440px]">
        <S />
      </div>
    ),
  ],
};
export const Empty: Story = {
  render: () => (
    <div className="max-w-[440px]">
      <EmptyList>대표님께 온 요청이 없습니다</EmptyList>
    </div>
  ),
};
