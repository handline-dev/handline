// One account's settings: each field saves on its own through account.set (authored: the value shows at once, a refusal puts it back).
import { useEffect, useState } from 'react';
import type { Account } from '@handline/client';
import { useConnection } from '@handline/app-react';
import { useServes, useWrite } from '@handline/app-data';
import { AccountLogin } from './AccountLogin.tsx';
import { AccountRemove } from './AccountRemove.tsx';
import { Field, Icon, Switch, NOTE } from './ui/index.ts';

export function AccountSettings({ account }: { account: Account }) {
  const save = useWrite('account.set', account.name);
  const { served } = useServes('account.set');
  const open = useConnection() === 'open';
  const off = !open ? 'daemon not connected' : served ? undefined : 'this daemon does not serve account.set';
  const [alias, setAlias] = useState(account.label === account.name ? '' : account.label);
  const [cap, setCap] = useState(account.cap === null ? '' : String(account.cap));
  const [end, setEnd] = useState(account.cycle.end_is_default ? '' : (account.cycle.end ?? ''));
  // Another client's change arrives through the topic and replaces what the box shows.
  useEffect(() => setAlias(account.label === account.name ? '' : account.label), [account.label, account.name]);
  useEffect(() => setCap(account.cap === null ? '' : String(account.cap)), [account.cap]);
  useEffect(() => setEnd(account.cycle.end_is_default ? '' : (account.cycle.end ?? '')), [account.cycle.end, account.cycle.end_is_default]);
  const pending = save.isPending;
  return (
    <form
      className="acct-settings flex flex-col gap-3"
      onSubmit={(e) => e.preventDefault()}
      aria-label={`Settings of ${account.label}`}
      data-pending={pending ? '' : undefined}
    >
      <Field
        label="Alias"
        value={alias}
        placeholder={account.name}
        disabled={!!off}
        title={off}
        onChange={(e) => setAlias(e.target.value)}
        onBlur={() => alias.trim() !== (account.label === account.name ? '' : account.label) && save.mutate({ account: account.name, label: alias.trim() })}
      />
      <Field
        label="Cap"
        unit="agents"
        type="number"
        min={1}
        value={cap}
        placeholder={String(account.cap_effective)}
        disabled={!!off}
        title={off ?? `The most agents mid-turn at once; now ${account.cap_effective}`}
        note={`now ${account.cap_effective}`}
        onChange={(e) => setCap(e.target.value)}
        onBlur={() => {
          const n = Number(cap);
          if (cap !== '' && Number.isInteger(n) && n >= 1 && n !== account.cap) save.mutate({ account: account.name, cap: n });
        }}
      />
      <div className="acct-switch flex items-center gap-2" title="A dead usage read may spend one real turn to fix itself">
        <Switch label="Usage recovery" on={account.recover} disabled={!!off} onChange={(on) => save.mutate({ account: account.name, recover: on })} />
        <span>Usage recovery</span>
      </div>
      <div className="acct-switch flex items-center gap-2">
        <Switch label="Ends this month" on={account.cycle.on} disabled={!!off} onChange={(on) => save.mutate({ account: account.name, cycle: on })} />
        <span>Ends this month</span>
      </div>
      <Field
        label="Last day"
        type="date"
        value={end}
        disabled={!!off}
        title={off ?? 'Blank means the next monthly anniversary'}
        onChange={(e) => setEnd(e.target.value)}
        onBlur={() =>
          end !== (account.cycle.end_is_default ? '' : (account.cycle.end ?? '')) && save.mutate({ account: account.name, cycle_end: end || null })
        }
      />
      <AccountLogin account={account} />
      <AccountRemove account={account} />
      {pending ? (
        <p className={NOTE} role="status">
          <Icon name="sending" size={14} />
          saving
        </p>
      ) : null}
      {off ? (
        <p className={`${NOTE} text-wait`}>
          <Icon name="warn" size={14} label="Locked" />
          Locked: {off}.
        </p>
      ) : null}
    </form>
  );
}
