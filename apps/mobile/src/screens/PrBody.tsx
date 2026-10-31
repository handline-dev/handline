// Words as Markdown, with each #N that is a pull request of the chat agent's repo a link to it on GitHub; any other #N stays plain text. Inside an agent page a T<n> and a .md path open as a tab.
import type { ReactNode } from 'react';
import { Linking, Text } from 'react-native';
import { findRefs } from '@handline/app-core';
import { usePullRequests } from '@handline/app-data';
import { usePalette } from '#src/theme.ts';
import { Markdown } from '#src/screens/Markdown.tsx';
import { fileTab, taskTab, useOpenInTab } from '#src/screens/AgentTabs.tsx';

const MD_PATH = /(?:[\w.-]+\/)*[\w.-]+\.md\b/g;

export function PrBody({
  id,
  agent,
  body,
  testID,
  breaks = true,
  selectable,
  ink,
}: {
  id: number | string;
  agent: string;
  body: string;
  testID?: string;
  breaks?: boolean;
  selectable?: boolean;
  ink?: string;
}) {
  const c = usePalette();
  const prs = usePullRequests(agent, body);
  const tabs = useOpenInTab();
  const plain = (s: string): ReactNode => {
    type Span = { start: number; end: number; onPress: () => void; testID: string };
    const spans: Span[] = [];
    for (const r of prs.repo ? findRefs(s, { pr: prs.isPr }).filter((r) => r.kind === 'pr') : [])
      spans.push({
        start: r.start,
        end: r.end,
        testID: `pr-${id}-${r.id}`,
        onPress: () => void Linking.openURL(`https://github.com/${prs.repo}/pull/${r.id}`),
      });
    if (tabs) {
      for (const r of findRefs(s, { task: () => true }).filter((r) => r.kind === 'task'))
        spans.push({ start: r.start, end: r.end, testID: `task-${id}-${r.id}`, onPress: () => tabs.open(taskTab(r.id)) });
      for (const m of s.matchAll(MD_PATH))
        spans.push({ start: m.index!, end: m.index! + m[0].length, testID: `file-${id}-${m[0]}`, onPress: () => tabs.open(fileTab(m[0])) });
    }
    const parts: ReactNode[] = [];
    let at = 0;
    for (const r of spans.sort((a, b) => a.start - b.start).filter((r, i, all) => !i || r.start >= all[i - 1]!.end)) {
      parts.push(s.slice(at, r.start));
      parts.push(
        <Text
          key={r.start}
          testID={r.testID}
          accessibilityRole="link"
          style={{ color: ink ?? c.accent, textDecorationLine: 'underline' }}
          onPress={r.onPress}
        >
          {s.slice(r.start, r.end)}
        </Text>,
      );
      at = r.end;
    }
    parts.push(s.slice(at));
    return parts;
  };
  return <Markdown testID={testID ?? `body-${id}`} text={body} breaks={breaks} plain={plain} selectable={selectable} cards ink={ink} />;
}
