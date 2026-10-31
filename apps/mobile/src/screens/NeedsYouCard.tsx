// What the agent waits on you for, at the bottom of its chat: kind, the one line and since when. A prompt the phone can answer is answered
// here (the card below it); one it cannot is the computer's to answer. The box leaves when needs_you turns null; the prompt slot stays
// mounted so "Answered elsewhere" outlives it.
import { Text, View } from 'react-native';
import { ago } from '@handline/app-core';
import type { NeedsYou, Prompt } from '@handline/client';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { PromptSlot } from '#src/screens/PromptCard.tsx';

export const KIND = {
  permission: 'Permission',
  question: 'Question',
  usage: 'Usage limit',
  dialog: 'Dialog',
  stuck: 'Blocked',
  trust: 'Folder trust',
  start_failed: "Won't start",
  no_session: 'No session',
} as const;

export function NeedsYouCard(props: { agent: string; needs: NeedsYou | null; prompt: Prompt | null }) {
  const c = usePalette();
  const { needs } = props;
  return (
    <View testID={needs ? 'needs-you' : undefined} accessibilityRole={needs ? 'alert' : undefined} style={{ gap: space.xs }}>
      {needs ? (
        <Text style={{ color: c.muted, fontSize: font.size.sm }}>
          <Text style={{ color: c.ink, fontWeight: '600' }}>Needs you</Text> · {KIND[needs.kind]} · {ago(needs.since)}
          {needs.text ? `: ${needs.text}` : !props.prompt ? ': answer at the computer' : ''}
        </Text>
      ) : null}
      <PromptSlot agent={props.agent} prompt={props.prompt} />
    </View>
  );
}
