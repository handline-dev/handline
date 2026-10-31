// The footer row under the composer: how much of the context window is used, the branch with the lines it changes, and the account.
// A number the daemon cannot measure is left out.
import { Text, View } from 'react-native';
import type { AgentDetail } from '@handline/client';
import { useAgentStrip, useServes, useWorktrees } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon, type IconName } from '#src/screens/Icon.tsx';

export function AgentStrip({ detail }: { detail: AgentDetail }) {
  const c = usePalette();
  const { served } = useServes('agent.strip');
  const strip = useAgentStrip(detail.name).data;
  const changed = useWorktrees({ owner: detail.name }).data?.worktrees.find((w) => w.path === detail.worktree?.path)?.changed ?? [];
  const ctx = detail.ctx_pct ?? strip?.ctx_pct;
  const added = changed.reduce((n, f) => n + f.added, 0);
  const deleted = changed.reduce((n, f) => n + f.deleted, 0);
  const account = strip?.account;
  if (!served || !strip) return null;
  const small = { color: c.muted, fontSize: font.size.xs } as const;
  const item = (testID: string, icon: IconName, text: string, shrink = false) => (
    <View key={testID} style={{ flexDirection: 'row', alignItems: 'center', gap: 4, flexShrink: shrink ? 1 : 0 }}>
      <Icon name={icon} decorative size={13} color={c.muted} />
      <Text testID={testID} numberOfLines={1} style={{ ...small, flexShrink: 1 }}>
        {text}
      </Text>
    </View>
  );
  return (
    <View
      testID="agent-strip"
      style={{ flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.lg, paddingBottom: space.sm }}
    >
      {ctx != null && item('strip-context', 'stats', `${Math.round(ctx)}% context`)}
      {detail.worktree &&
        item('strip-branch', 'worktrees', `${detail.worktree.branch}${changed.length ? ` +${added} −${deleted}` : ''}`, true)}
      {account && item('strip-account', 'owner', account)}
    </View>
  );
}
