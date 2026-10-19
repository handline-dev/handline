// An agent's state as its own shape, so no two states differ by colour alone (the web's ui/State, the same icons).
import { View } from 'react-native';
import { dotOf, type Dot } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { dotColor, usePalette } from '../theme.ts';
import { Icon, type IconName } from './Icon.tsx';

const MARKS: Record<Dot, { icon: IconName; label: string }> = {
  running: { icon: 'working', label: 'Working' },
  waking: { icon: 'booting', label: 'Starting' },
  waiting: { icon: 'needs', label: 'Needs you' },
  blocked: { icon: 'blocked', label: 'Blocked' },
  idle: { icon: 'idle', label: 'Idle' },
  parked: { icon: 'asleep', label: 'Parked' },
  none: { icon: 'noTerminal', label: 'Stopped' },
};

export function StateMark({ agent }: { agent: Pick<AgentView, 'state' | 'has_dialog' | 'block'> }) {
  const c = usePalette();
  const dot = dotOf(agent);
  return (
    <View testID={`state-${dot}`} accessible accessibilityLabel={MARKS[dot].label} style={{ width: 16, alignItems: 'center' }}>
      <Icon name={MARKS[dot].icon} label={MARKS[dot].label} decorative color={dotColor(c, dot)} />
    </View>
  );
}
