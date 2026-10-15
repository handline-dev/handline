// The one control that flips an agent's tab between the chat and Claude Code: a small icon with no label, in the bottom bar of either view.
import { useWorkspaceIfAny } from './Workspace.tsx';
import { Button } from './ui/index.ts';

const KEY = /Mac|iPhone|iPad/.test(globalThis.navigator?.platform ?? '') ? '⌘J' : 'Ctrl+J';

export function ViewSwitch({ to }: { to: 'chat' | 'terminal' }) {
  const ws = useWorkspaceIfAny();
  if (!ws || (to === 'terminal' && !ws.detail.terminal && !ws.detail.structured)) return null;
  const label = to === 'chat' ? 'Back to the chat' : ws.detail.structured ? 'Transcript' : 'Claude Code';
  return <Button className="ml-auto" size="sm" icon={to === 'chat' ? 'chat' : 'terminal'} label={`${label} (${KEY})`} onClick={ws.flip} />;
}
