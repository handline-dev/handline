// What the agent waits on you for, at the bottom of its chat: kind, the one line and since when. A prompt the phone can answer is answered
// here (the card below it); one it cannot is the computer's to answer. The box leaves when needs_you turns null; the prompt slot stays
// mounted so "Answered elsewhere" outlives it.
import { Text, View } from 'react-native';
import { ago } from '@handline/app-core';
import type { NeedsYou, Prompt } from '@handline/client';
import { radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { PromptSlot } from '#src/screens/PromptCard.tsx';

export const KIND = { permission: 'Permission', question: 'Question', usage: 'Usage limit', dialog: 'Dialog' } as const;

export function NeedsYouCard(props: { agent: string; needs: NeedsYou | null; prompt: Prompt | null }) {
  const c = usePalette();
  const { needs } = props;
  return (
    <View
      testID={needs ? 'needs-you' : undefined}
      accessibilityRole={needs ? 'alert' : undefined}
      style={
        needs && {
          margin: space.sm,
          padding: space.md,
          gap: space.xs,
          borderWidth: 1,
          borderColor: c.wait,
          borderRadius: radius.dialog,
          backgroundColor: c.panel,
        }
      }
    >
      {needs ? (
        <Text style={{ color: c.wait, fontWeight: '700' }}>
          Needs you · {KIND[needs.kind]} · {ago(needs.since)}
        </Text>
      ) : null}
      {needs?.text ? <Text style={{ color: c.ink }}>{needs.text}</Text> : null}
      <PromptSlot agent={props.agent} prompt={props.prompt} />
      {needs && !props.prompt ? (
        <Text testID="needs-you-computer" style={{ color: c.muted }}>
          Answer at the computer.
        </Text>
      ) : null}
    </View>
  );
}
