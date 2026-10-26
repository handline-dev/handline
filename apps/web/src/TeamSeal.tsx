// Seal the hand-edited team.yaml and agent.yaml files (founder only): a dry run lists what would be sealed, then the founder confirms.
import { useEffect, useState } from 'react';
import { useHello } from '@handline/app-react';
import { classify, sentence, useOff, useWrite } from '@handline/app-data';
import { Button, Dialog, Icon, NOTE } from '#src/ui/index.ts';

function SealDialog({ onClose }: { onClose: () => void }) {
  const plan = useWrite('team.seal');
  const seal = useWrite('team.seal');
  const { mutate: dry } = plan;
  useEffect(() => dry({ dry_run: true }), [dry]);
  const lines = (plan.data?.plan ?? '').split('\n').filter(Boolean);
  const failed = plan.error ?? seal.error;
  return (
    <Dialog
      title="Seal the team files"
      onClose={onClose}
      action={{
        label: seal.isPending ? 'Sealing…' : 'Seal',
        disabled: !plan.data || seal.isPending,
        onClick: () => seal.mutate({}, { onSuccess: onClose }),
      }}
    >
      {plan.isPending ? <p className="muted">Checking the files…</p> : null}
      {lines.length ? (
        <ul className="seal-plan" aria-label="What sealing would do">
          {lines.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      ) : null}
      {failed ? (
        <p className={`${NOTE} text-bad`} role="alert">
          <Icon name="failed" size={14} label="Error" />
          {sentence(classify(failed))}
        </p>
      ) : null}
      <p className="muted">Read the files over first: sealing accepts them as they are now.</p>
    </Dialog>
  );
}

export function TeamSeal() {
  const founder = useHello()?.role === 'founder';
  const off = useOff('team.seal');
  const [asking, setAsking] = useState(false);
  if (!founder) return null;
  return (
    <>
      <Button
        label="Seal…"
        disabled={!!off}
        title={off ?? 'Accept your hand edits to team.yaml and the agent files'}
        onClick={() => setAsking(true)}
      />
      {asking ? <SealDialog onClose={() => setAsking(false)} /> : null}
    </>
  );
}
