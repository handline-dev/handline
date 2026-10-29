import type { Meta, StoryObj } from '@storybook/react-vite';
import { Id, Link, SeeInFull, Who } from '#src/ds/Link.tsx';

const meta: Meta<typeof Link> = { title: 'Parts/Link, ID, name', component: Link };
export default meta;
type Story = StoryObj;

export const TaskId: Story = { render: () => <Id href="#T7016">T7016</Id> };
export const PrNumber: Story = { render: () => <Link href="#798">#798</Link> };
export const AgentName: Story = { render: () => <Who href="#pc-chat">pc-chat</Who> };
export const SeeItInFull: Story = {
  render: () => (
    <div className="flex w-80">
      <SeeInFull href="#T7070">Open T7070</SeeInFull>
    </div>
  ),
};
export const InText: Story = {
  render: () => (
    <p>
      <Id href="#T7016">T7016</Id> merged by <Who href="#handline">handline</Who> in <Link href="#798">#798</Link>
    </p>
  ),
};
