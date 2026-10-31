// The footer row under the composer: how much of the context window is used, the branch with the lines it changes, and the account.
// A number the daemon cannot measure is left out.
import { Text, View } from 'react-native';
import type { AgentDetail } from '@handline/client';
import { useAgentStrip, useServes, useWorktrees } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';

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
  return (
    <View
      testID="agent-strip"
      style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.lg, paddingBottom: space.sm }}
    >
      {ctx != null && (
        <Text testID="strip-context" style={small}>
          {Math.round(ctx)}% context
        </Text>
      )}
      {detail.worktree && (
        <Text testID="strip-branch" numberOfLines={1} style={{ ...small, flexShrink: 1 }}>
          {ctx != null ? '· ' : ''}
          {detail.worktree.branch}
          {changed.length ? ` +${added} −${deleted}` : ''}
        </Text>
      )}
      {account && (
        <Text testID="strip-account" numberOfLines={1} style={small}>
          · {account}
        </Text>
      )}
    </View>
  );
}
