import type { Meta, StoryObj } from '@storybook/react-vite';
import { SquareCheck, Terminal } from 'lucide-react';
import { Button } from '#src/ds/Button.tsx';
import { Chip } from '#src/ds/Chip.tsx';
import { Link, SeeInFull, Who } from '#src/ds/Link.tsx';
import { Answer, ItemHeader } from '#src/ds/Item.tsx';

const meta: Meta<typeof ItemHeader> = { title: 'Patterns/Header and answer', component: ItemHeader };
export default meta;
type Story = StoryObj;

export const TaskRequest: Story = {
  render: () => (
    <div className="max-w-[760px]">
      <ItemHeader
        source={
          <Chip icon={SquareCheck}>
            Task <span className="font-mono">T7070</span>
          </Chip>
        }
        who={<Who>handline</Who>}
        when="40m"
        title={
          <>
            Merge PR <Link>#798</Link>, <Link>#799</Link>
          </>
        }
      />
      <p className="mt-3">Both PRs are green and audit-web passed them. Merging puts the build stamp fix on main.</p>
      <Answer kind="Allow" ask="Merge both into main." full={<SeeInFull>Open T7070</SeeInFull>}>
        <Button kind="primary" label="Merge" />
        <Button label="Not now" />
      </Answer>
    </div>
  ),
};
export const PermissionCommand: Story = {
  render: () => (
    <div className="max-w-[760px]">
      <ItemHeader source={<Chip icon={Terminal}>Session architect</Chip>} when="2m" title="Run a command" />
      <Answer kind="Allow" code ask="go test ./... in daemon" full={<SeeInFull>View in chat</SeeInFull>}>
        <Button kind="primary" label="Allow" />
        <Button label="Deny" />
      </Answer>
    </div>
  ),
};
