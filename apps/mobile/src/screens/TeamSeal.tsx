// Seal the hand-edited team files (docs/pc-app-gaps.md K7, founder only): a dry run lists what would be sealed, then Seal, and the daemon's line.
import { useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import { classify, sentence, useOff, useWrite } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import { space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button } from '#src/screens/parts.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';

function SealSheet({ onClose }: { onClose: () => void }) {
  const c = usePalette();
  const plan = useWrite('team.seal');
  const seal = useWrite('team.seal');
  const { mutate: dry } = plan;
  useEffect(() => dry({ dry_run: true }), [dry]);
  const lines = (plan.data?.plan ?? '').split('\n').filter(Boolean);
  const failed = plan.error ?? seal.error;
  return (
    <Sheet
      id="seal"
      label="Seal the team files"
      submit={seal.isPending ? 'Sealing…' : 'Seal'}
      busy={!plan.data || seal.isPending}
      error={failed ? sentence(classify(failed)) : undefined}
      onCancel={onClose}
      onSubmit={seal.isSuccess ? undefined : () => seal.mutate({})}
    >
      {plan.isPending ? <Text style={{ color: c.muted }}>Checking the files…</Text> : null}
      {lines.length ? (
        <View testID="seal-plan" style={{ gap: space.xs }}>
          {lines.map((l) => (
            <Text key={l} style={{ color: c.ink }}>
              {l}
            </Text>
          ))}
        </View>
      ) : null}
      {seal.isSuccess ? (
        <Text testID="seal-said" style={{ color: c.run, fontWeight: '600' }}>
          {seal.data?.said ?? 'Sealed.'}
        </Text>
      ) : (
        <Text style={{ color: c.muted }}>Read the files over first: sealing accepts them as they are now.</Text>
      )}
    </Sheet>
  );
}

export function TeamSeal() {
  const founder = useSelector((s) => s.client.hello?.role) === 'founder';
  const off = useOff('team.seal');
  const [asking, setAsking] = useState(false);
  if (!founder || off) return null;
  return (
    <>
      <Button testID="seal-open" label="Seal…" onPress={() => setAsking(true)} style={{ alignSelf: 'flex-start' }} />
      {asking ? <SealSheet onClose={() => setAsking(false)} /> : null}
    </>
  );
}
