// The three answers for a worktree that holds work. The safe one is filled, the one that loses files is the danger button and asks once more
// ("for good?") before it acts. The words and the order are the PC's for the decision card; the delete sheet keeps Save first because the
// person already asked to delete.
import { useState } from 'react';
import { Text, View } from 'react-native';
import type { WorktreeAnswer } from '@handline/app-core';
import { space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button } from '#src/screens/parts.tsx';

export interface AnswerWords {
  /** Top to bottom; the first is filled. */
  order: readonly WorktreeAnswer[];
  labels: Record<WorktreeAnswer, string>;
  /** What the second ask says and its button: the verb the losing answer was named with. */
  sure: { text: string; go: string };
}
/** The decision the daemon opens when nobody was there to ask. */
export const CARD_WORDS: AnswerWords = {
  order: ['keep', 'save', 'force'],
  labels: { keep: 'Keep it', save: 'Save to a branch, then remove', force: 'Remove anyway' },
  sure: { text: 'Remove for good? Uncommitted files are lost.', go: 'Remove' },
};
/** The delete pop on an agent: the answer is part of the delete. */
export const POP_WORDS: AnswerWords = {
  order: ['save', 'keep', 'force'],
  labels: { save: 'Save to a branch, then delete', keep: 'Delete, keep the worktree', force: 'Delete and lose the files' },
  sure: { text: 'Delete for good? Uncommitted files are lost.', go: 'Delete' },
};

export function WorktreeAnswers(props: { id: string; words: AnswerWords; busy?: boolean; onPick: (a: WorktreeAnswer) => void }) {
  const c = usePalette();
  const { words } = props;
  const [sure, setSure] = useState(false);
  if (sure)
    return (
      <View style={{ gap: space.sm }}>
        <Text testID={`${props.id}-sure`} accessibilityRole="alert" style={{ color: c.block }}>
          {words.sure.text}
        </Text>
        <Button
          testID={`${props.id}-sure-go`}
          kind="no"
          label={words.sure.go}
          disabled={props.busy}
          onPress={() => props.onPick('force')}
        />
        <Button testID={`${props.id}-sure-cancel`} label="Cancel" onPress={() => setSure(false)} />
      </View>
    );
  return (
    <View style={{ gap: space.sm }}>
      {words.order.map((a, i) => (
        <Button
          key={a}
          testID={`${props.id}-${a}`}
          kind={a === 'force' ? 'no' : i === 0 ? 'yes' : 'plain'}
          label={words.labels[a]}
          disabled={props.busy}
          onPress={() => (a === 'force' ? setSure(true) : props.onPick(a))}
        />
      ))}
    </View>
  );
}
