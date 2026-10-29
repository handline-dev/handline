import type { Meta, StoryObj } from '@storybook/react-vite';
import { ChevronDown, Gauge, GitBranch, Mic, Paperclip, UserRound } from 'lucide-react';
import { Button } from '#src/ds/Button.tsx';
import { AgentSay, Composer, ComposerFoot, FootCell, UserBubble, WorkLine } from '#src/ds/Chat.tsx';
import { Id } from '#src/ds/Link.tsx';

const meta: Meta<typeof Composer> = { title: 'Patterns/Chat and composer', component: Composer };
export default meta;
type Story = StoryObj;

const tools = (
  <>
    <Button kind="quiet" iconOnly icon={Paperclip} label="Attach" />
    <Button kind="quiet" iconOnly icon={Mic} label="Dictate" />
  </>
);
const model = (
  <>
    sonnet · medium <ChevronDown width={13} height={13} strokeWidth={1.75} aria-hidden />
  </>
);

export const Conversation: Story = {
  render: () => (
    <div className="max-w-[760px]">
      <UserBubble>새로 태스크 만드는 거나 상태 변경은 한 줄로 묶어서 보여줘</UserBubble>
      <WorkLine>Worked for 2m 10s</WorkLine>
      <AgentSay>
        받아 적었습니다(채팅 요구사항 8, 9). 변경은 접힌 한 줄로 묶습니다. <Id>K7020</Id>
      </AgentSay>
      <div className="mt-4">
        <Composer placeholder="Send a message…" left={model} tools={tools} />
        <ComposerFoot>
          <FootCell icon={Gauge}>13% context</FootCell>
          <FootCell icon={GitBranch}>story/update-daemon-set +1,091 −238</FootCell>
          <FootCell icon={UserRound}>sigma</FootCell>
        </ComposerFoot>
      </div>
    </div>
  ),
};
export const MyMessage: Story = { render: () => <UserBubble>Merge both into main</UserBubble> };
export const AgentWords: Story = {
  render: () => (
    <AgentSay>
      Both PRs are green. <Id>K7070</Id> is next.
    </AgentSay>
  ),
};
export const ComposerEmpty: Story = { args: { placeholder: 'Send a message…', left: model, tools } };
export const ComposerTyping: Story = { args: { defaultValue: 'Run the audit again, then merge', left: model, tools } };
