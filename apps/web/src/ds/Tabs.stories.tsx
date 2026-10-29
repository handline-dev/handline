import type { Meta, StoryObj } from '@storybook/react-vite';
import { MessageSquare } from 'lucide-react';
import { NewTab, Tab, Tabs } from '#src/ds/Tabs.tsx';

const meta: Meta<typeof Tabs> = { title: 'Parts/Tabs', component: Tabs };
export default meta;
type Story = StoryObj;

export const AgentWindow: Story = {
  render: () => (
    <div className="max-w-[640px]">
      <Tabs owner="handline">
        <Tab icon={MessageSquare} active>
          Chat
        </Tab>
        <Tab>Tasks</Tab>
        <Tab mono onClose={() => {}}>
          T7013
        </Tab>
        <Tab mono preview onClose={() => {}}>
          T7016
        </Tab>
        <NewTab />
      </Tabs>
    </div>
  ),
};
export const PinnedTask: Story = {
  render: () => (
    <div className="max-w-[640px]">
      <Tabs>
        <Tab>Chat</Tab>
        <Tab mono active onClose={() => {}}>
          T7013
        </Tab>
      </Tabs>
    </div>
  ),
};
export const PreviewTask: Story = {
  render: () => (
    <div className="max-w-[640px]">
      <Tabs>
        <Tab>Chat</Tab>
        <Tab mono preview active onClose={() => {}}>
          T7016
        </Tab>
      </Tabs>
    </div>
  ),
};
