// Words as Markdown, with each #N that is a pull request of the chat agent's repo a link to it on GitHub; any other #N stays plain text.
import type { ReactNode } from 'react';
import { Linking, Text } from 'react-native';
import { findRefs } from '@handline/app-core';
import { usePullRequests } from '@handline/app-data';
import { usePalette } from '#src/theme.ts';
import { Markdown } from '#src/screens/Markdown.tsx';

export function PrBody({ id, agent, body, testID, breaks = true }: { id: number | string; agent: string; body: string; testID?: string; breaks?: boolean }) {
  const c = usePalette();
  const prs = usePullRequests(agent, body);
  const plain = (s: string): ReactNode => {
    const refs = prs.repo ? findRefs(s, { pr: prs.isPr }).filter((r) => r.kind === 'pr') : [];
    const parts: ReactNode[] = [];
    let at = 0;
    for (const r of refs) {
      parts.push(s.slice(at, r.start));
      parts.push(
        <Text
          key={r.start}
          testID={`pr-${id}-${r.id}`}
          accessibilityRole="link"
          style={{ color: c.accent, textDecorationLine: 'underline' }}
          onPress={() => void Linking.openURL(`https://github.com/${prs.repo}/pull/${r.id}`)}
        >
          {s.slice(r.start, r.end)}
        </Text>,
      );
      at = r.end;
    }
    parts.push(s.slice(at));
    return parts;
  };
  return <Markdown testID={testID ?? `body-${id}`} text={body} breaks={breaks} plain={plain} />;
}
