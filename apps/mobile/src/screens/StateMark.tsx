// An agent's state as its own shape, so no two states differ by colour alone (the web's ui/State, drawn as text since the phone has no icon set).
import { Text } from 'react-native';
import { dotOf, type Dot } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { dotColor, usePalette } from '../theme.ts';

const MARKS: Record<Dot, { glyph: string; label: string }> = {
  running: { glyph: '▶', label: 'Working' },
  waking: { glyph: '◔', label: 'Starting' },
  waiting: { glyph: '!', label: 'Needs you' },
  blocked: { glyph: '✕', label: 'Blocked' },
  idle: { glyph: '○', label: 'Idle' },
  parked: { glyph: '❚❚', label: 'Parked' },
  none: { glyph: '–', label: 'No terminal' },
};

export function StateMark({ agent }: { agent: Pick<AgentView, 'state' | 'has_dialog' | 'block'> }) {
  const c = usePalette();
  const dot = dotOf(agent);
  return (
    <Text
      testID={`state-${dot}`}
      accessibilityLabel={MARKS[dot].label}
      style={{ width: 16, textAlign: 'center', fontWeight: '700', color: dotColor(c, dot) }}
    >
      {MARKS[dot].glyph}
    </Text>
  );
}
