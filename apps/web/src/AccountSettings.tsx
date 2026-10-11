// One account's settings: each field saves on its own through account.set (authored: the value shows at once, a refusal puts it back).
import { useEffect, useState } from 'react';
import type { Account } from '@handline/client';
import { useConnection } from '@handline/app-react';
import { useServes, useWrite } from '@handline/app-data';
import { AccountRemove } from './AccountRemove.tsx';
import { Field, Icon, Switch } from './ui/index.ts';

export function AccountSettings({ account: a }: { account: Account }) {
  const save = useWrite('account.set', a.name);
  const { served } = useServes('account.set');
  const open = useConnection() === 'open';
  const off = !open ? 'daemon not connected' : served ? undefined : 'this daemon does not serve account.set';
  const [alias, setAlias] = useState(a.label === a.name ? '' : a.label);
  const [cap, setCap] = useState(a.cap === null ? '' : String(a.cap));
  const [end, setEnd] = useState(a.cycle.end_is_default ? '' : (a.cycle.end ?? ''));
  // Another client's change arrives through the topic and replaces what the box shows.
  useEffect(() => setAlias(a.label === a.name ? '' : a.label), [a.label, a.name]);
  useEffect(() => setCap(a.cap === null ? '' : String(a.cap)), [a.cap]);
  useEffect(() => setEnd(a.cycle.end_is_default ? '' : (a.cycle.end ?? '')), [a.cycle.end, a.cycle.end_is_default]);
  const pending = save.isPending;
  return (
    <form
      className="acct-settings flex flex-col gap-3"
      onSubmit={(e) => e.preventDefault()}
      aria-label={`Settings of ${a.label}`}
      data-pending={pending ? '' : undefined}
    >
      <Field
        label="Alias"
        value={alias}
        placeholder={a.name}
        disabled={!!off}
        title={off}
        onChange={(e) => setAlias(e.target.value)}
        onBlur={() => alias.trim() !== (a.label === a.name ? '' : a.label) && save.mutate({ account: a.name, label: alias.trim() })}
      />
      <Field
        label="Cap"
        unit="agents"
        type="number"
        min={1}
        value={cap}
        placeholder={String(a.cap_effective)}
        disabled={!!off}
        title={off ?? `The most agents mid-turn at once; now ${a.cap_effective}`}
        note={`now ${a.cap_effective}`}
        onChange={(e) => setCap(e.target.value)}
        onBlur={() => {
          const n = Number(cap);
          if (cap !== '' && Number.isInteger(n) && n >= 1 && n !== a.cap) save.mutate({ account: a.name, cap: n });
        }}
      />
      <div className="acct-switch flex items-center gap-2" title="A dead usage read may spend one real turn to fix itself">
        <Switch label="Usage recovery" on={a.recover} disabled={!!off} onChange={(on) => save.mutate({ account: a.name, recover: on })} />
        <span>Usage recovery</span>
      </div>
      <div className="acct-switch flex items-center gap-2">
        <Switch label="Ends this month" on={a.cycle.on} disabled={!!off} onChange={(on) => save.mutate({ account: a.name, cycle: on })} />
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
          end !== (a.cycle.end_is_default ? '' : (a.cycle.end ?? '')) && save.mutate({ account: a.name, cycle_end: end || null })
        }
      />
      <AccountRemove account={a} />
      {pending ? (
        <p className="ui-note" role="status">
          <Icon name="sending" size={14} />
          saving
        </p>
      ) : null}
      {off ? (
        <p className="ui-note warn">
          <Icon name="warn" size={14} label="Locked" />
          Locked: {off}.
        </p>
      ) : null}
    </form>
  );
}
