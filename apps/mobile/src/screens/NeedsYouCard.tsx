// What the agent waits on you for, at the bottom of its chat: kind, the one line and since when. A prompt the phone can answer is answered
// here (the card below it); one it cannot is the computer's to answer. It leaves when needs_you turns null.
import { Text, View } from 'react-native';
import { ago } from '@handline/app-core';
import type { NeedsYou, Prompt } from '@handline/client';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { PromptSlot } from './PromptCard.tsx';

export const KIND = { permission: 'Permission', question: 'Question', usage: 'Usage limit', dialog: 'Dialog' } as const;

export function NeedsYouCard(props: { agent: string; needs: NeedsYou; prompt: Prompt | null }) {
  const c = usePalette();
  const { needs } = props;
  return (
    <View
      testID="needs-you"
      accessibilityRole="alert"
      style={{
        margin: space.sm,
        padding: space.md,
        gap: space.xs,
        borderWidth: 1,
        borderColor: c.wait,
        borderRadius: radius.lg,
        backgroundColor: c.panel,
      }}
    >
      <Text style={{ color: c.wait, fontWeight: '700' }}>
        Needs you · {KIND[needs.kind]} · {ago(needs.since)}
      </Text>
      {needs.text ? <Text style={{ color: c.ink }}>{needs.text}</Text> : null}
      {props.prompt ? (
        <PromptSlot agent={props.agent} prompt={props.prompt} />
      ) : (
        <Text testID="needs-you-computer" style={{ color: c.muted }}>
          Answer at the computer.
        </Text>
      )}
    </View>
  );
}
