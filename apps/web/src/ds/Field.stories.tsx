import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { Check, Input, Segmented } from '#src/ds/Field.tsx';

const meta: Meta<typeof Input> = { title: 'Parts/Input and check', component: Input };
export default meta;
type Story = StoryObj;

export const Empty: Story = { args: { placeholder: 'Or write an answer…', 'aria-label': 'Answer' } };
export const Filled: Story = { args: { defaultValue: 'Merge both into main', 'aria-label': 'Answer' } };
export const Disabled: Story = { args: { placeholder: 'Or write an answer…', disabled: true, 'aria-label': 'Answer' } };
export const CheckList: Story = {
  render: () => (
    <div className="max-w-[520px]">
      <Check label="handline.ux.chat" hint="Chat" defaultChecked />
      <Check label="handline.ux.work" hint="Tasks and requests" defaultChecked />
      <Check label="handline.ux.phone" hint="The phone app" />
    </div>
  ),
};
export const SegmentedSwitch: Story = {
  render: function Render() {
    const [a, setA] = useState('open');
    const [b, setB] = useState('for-you');
    return (
      <div className="flex gap-4">
        <Segmented
          label="State"
          value={a}
          onChange={setA}
          choices={[
            { value: 'open', label: 'Open' },
            { value: 'closed', label: 'Closed' },
          ]}
        />
        <Segmented
          label="Who"
          value={b}
          onChange={setB}
          choices={[
            { value: 'for-you', label: 'For you 11' },
            { value: 'from-you', label: 'From you' },
          ]}
        />
      </div>
    );
  },
};
