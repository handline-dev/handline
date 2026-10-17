// A message's words, with each #N that is a pull request of the chat agent's repo a link to it on GitHub; any other #N stays plain text.
import type { ReactNode } from 'react';
import { Linking, Text } from 'react-native';
import { findRefs } from '@handline/app-core';
import { usePullRequests } from '@handline/app-data';
import { usePalette } from '../theme.ts';

export function PrBody({ id, agent, body }: { id: number; agent: string; body: string }) {
  const c = usePalette();
  const prs = usePullRequests(agent, body);
  const refs = prs.repo ? findRefs(body, { pr: prs.isPr }).filter((r) => r.kind === 'pr') : [];
  const parts: ReactNode[] = [];
  let at = 0;
  for (const r of refs) {
    parts.push(body.slice(at, r.start));
    parts.push(
      <Text
        key={r.start}
        testID={`pr-${id}-${r.id}`}
        accessibilityRole="link"
        style={{ color: c.accent, textDecorationLine: 'underline' }}
        onPress={() => void Linking.openURL(`https://github.com/${prs.repo}/pull/${r.id}`)}
      >
        {body.slice(r.start, r.end)}
      </Text>,
    );
    at = r.end;
  }
  parts.push(body.slice(at));
  return (
    <Text testID={`body-${id}`} style={{ color: c.ink }}>
      {parts}
    </Text>
  );
}
