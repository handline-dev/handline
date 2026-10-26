// The one control that flips an agent's tab between the chat and Claude Code: a small icon with no label, in the bottom bar of either view.
// Claude Code has no tab to carry its badges, so on the way there the icon carries them: what the terminal waits on, and a dot when it needs someone.
import { badgesOf, needsAttention } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { useWorkspaceIfAny } from '#src/Workspace.tsx';
import { Badges } from '#src/Badges.tsx';
import { Button } from '#src/ui/index.ts';
import { IS_MAC } from '#src/terminal/core.ts';

// From the terminal view the key is the one the terminal gives up (Ctrl+Shift+J off a Mac).
const keyFor = (to: 'chat' | 'terminal') => (IS_MAC ? '⌘J' : to === 'chat' ? 'Ctrl+Shift+J' : 'Ctrl+J');

export function ViewSwitch({ to }: { to: 'chat' | 'terminal' }) {
  const ws = useWorkspaceIfAny();
  const me = useStore().me;
  if (!ws) return null;
  const label = to === 'chat' ? 'Back to the chat' : ws.detail.structured ? 'Transcript' : 'Claude Code';
  const badges = to === 'terminal' ? badgesOf(ws.detail, me) : [];
  const attention = needsAttention(badges);
  return (
    <span className="ml-auto inline-flex items-center">
      <Badges badges={badges} onClick={{ dialog: ws.flip }} />
      <span className="relative inline-flex">
        <Button size="sm" icon={to === 'chat' ? 'chat' : 'terminal'} label={`${label} (${keyFor(to)})`} onClick={ws.flip} />
        {attention ? (
          <span className="unread-dot absolute top-0.5 right-0.5 h-[7px] w-[7px] rounded-full bg-wait" title="Needs you" />
        ) : null}
      </span>
    </span>
  );
}
