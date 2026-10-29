import type { Meta, StoryObj } from '@storybook/react-vite';
import { Mic, Paperclip, Send, X } from 'lucide-react';
import { Button } from '#src/ds/Button.tsx';

const meta: Meta<typeof Button> = { title: 'Parts/Button', component: Button, args: { label: 'Approve' } };
export default meta;
type Story = StoryObj;

export const Primary: Story = { args: { kind: 'primary' } };
export const Secondary: Story = { args: { label: 'Reject' } };
export const Quiet: Story = { args: { kind: 'quiet', label: 'Not now' } };
export const Danger: Story = { args: { kind: 'danger', label: 'Delete…' } };
export const Disabled: Story = { args: { label: 'Disabled', disabled: true } };
export const Small: Story = {
  render: () => (
    <div className="flex gap-3">
      <Button kind="primary" size="s" label="Allow" />
      <Button size="s" label="Deny" />
    </div>
  ),
};
export const Large: Story = { args: { kind: 'primary', size: 'l', label: 'Send', icon: Send } };
export const IconOnly: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Button kind="quiet" iconOnly icon={Paperclip} label="Attach" />
      <Button kind="quiet" iconOnly icon={Mic} label="Dictate" />
      <Button kind="quiet" size="s" iconOnly icon={X} label="Close" />
    </div>
  ),
};
