// Remove one account (founder only): a dry run lists what would happen and which agents sit on it, then the founder types the name to confirm.
import { useEffect, useState } from 'react';
import type { Account } from '@handline/client';
import { useHello } from '@handline/app-react';
import { classify, sentence, useOff, useWrite } from '@handline/app-data';
import { Button, Dialog, Field, Icon, NOTE } from '#src/ui/index.ts';
import { AnimatedList } from '#src/AnimatedList.tsx';

function RemoveDialog({ account: a, onClose }: { account: Account; onClose: () => void }) {
  const plan = useWrite('account.remove', a.name);
  const remove = useWrite('account.remove', a.name);
  const [typed, setTyped] = useState('');
  const { mutate: dry } = plan;
  useEffect(() => dry({ account: a.name, dry_run: true }), [dry, a.name]);
  const would = plan.data?.would_remove === true;
  const lines = plan.data?.plan ?? [];
  const failed = plan.error ?? remove.error;
  return (
    <Dialog
      title={`Remove ${a.label}`}
      onClose={onClose}
      action={{
        label: remove.isPending ? 'Removing…' : 'Remove account',
        disabled: !would || typed !== a.name || remove.isPending,
        onClick: () => remove.mutate({ account: a.name, confirm: typed }, { onSuccess: onClose }),
      }}
    >
      {plan.isPending ? <p className="muted">Checking what removing it would do…</p> : null}
      {a.agents.length ? (
        <p className={`${NOTE} text-wait`}>
          <Icon name="warn" size={14} label="Warning" />
          On it now: {a.agents.join(', ')}. Move them off first.
        </p>
      ) : null}
      {lines.length ? (
        <ul className="acct-plan" aria-label="What removing would do">
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
      {would ? (
        <Field
          label={`Type ${a.name} to confirm`}
          value={typed}
          autoComplete="off"
          onChange={(e) => setTyped(e.target.value)}
          note="Its directory is moved aside, not deleted."
        />
      ) : null}
    </Dialog>
  );
}

export function AccountRemove({ account: a }: { account: Account }) {
  const founder = useHello()?.role === 'founder';
  const off = useOff('account.remove');
  const [asking, setAsking] = useState(false);
  if (!founder) return null;
  return (
    <div className="acct-remove">
      <Button label="Remove account…" disabled={!!off} title={off} onClick={() => setAsking(true)} />
      <AnimatedList>{asking ? <RemoveDialog key="RemoveDialog" account={a} onClose={() => setAsking(false)} /> : null}</AnimatedList>
    </div>
  );
}
