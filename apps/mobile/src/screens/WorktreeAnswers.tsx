// The three answers for a worktree that holds work: save it to a branch then delete, delete it with what it holds, or leave it. Save is the
// filled one (nothing is lost); the destructive one is never highlighted. Labels differ by where it is asked, the words sent do not.
import { View } from 'react-native';
import type { WorktreeAnswer } from '@handline/app-core';
import { space } from '@handline/tokens';
import { Button } from '#src/screens/parts.tsx';

export type AnswerLabels = Record<WorktreeAnswer, string>;
/** The delete pop on an agent: the answer is part of the delete. */
export const POP_LABELS: AnswerLabels = { save: 'Save to a branch, delete', force: 'Delete and lose them', keep: 'Delete, keep worktree' };
/** The decision the daemon opens when nobody was there to ask. */
export const CARD_LABELS: AnswerLabels = { save: 'Save to a branch, then delete', force: 'Force delete', keep: 'Keep the worktree' };

export function WorktreeAnswers(props: { id: string; labels: AnswerLabels; busy?: boolean; onPick: (a: WorktreeAnswer) => void }) {
  return (
    <View style={{ gap: space.sm }}>
      {(['save', 'force', 'keep'] as const).map((a) => (
        <Button
          key={a}
          testID={`${props.id}-${a}`}
          kind={a === 'save' ? 'yes' : 'plain'}
          label={props.labels[a]}
          disabled={props.busy}
          onPress={() => props.onPick(a)}
        />
      ))}
    </View>
  );
}
