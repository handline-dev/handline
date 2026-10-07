// One account's settings: each field saves on its own through account.set (authored: the value shows at once, a refusal puts it back).
import { useEffect, useState } from 'react';
import type { Account } from '@handline/client';
import { useConnection } from '@handline/app-react';
import { useServes, useWrite } from '@handline/app-data';

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
      className="acct-settings"
      onSubmit={(e) => e.preventDefault()}
      aria-label={`Settings of ${a.label}`}
      data-pending={pending ? '' : undefined}
    >
      <label>
        Alias
        <input
          value={alias}
          placeholder={a.name}
          disabled={!!off}
          title={off}
          onChange={(e) => setAlias(e.target.value)}
          onBlur={() => alias.trim() !== (a.label === a.name ? '' : a.label) && save.mutate({ account: a.name, label: alias.trim() })}
        />
      </label>
      <label>
        Cap <span className="muted">(most agents mid-turn at once; now {a.cap_effective})</span>
        <input
          type="number"
          min={1}
          value={cap}
          placeholder={String(a.cap_effective)}
          disabled={!!off}
          title={off}
          onChange={(e) => setCap(e.target.value)}
          onBlur={() => {
            const n = Number(cap);
            if (cap !== '' && Number.isInteger(n) && n >= 1 && n !== a.cap) save.mutate({ account: a.name, cap: n });
          }}
        />
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={a.recover}
          disabled={!!off}
          title={off}
          onChange={(e) => save.mutate({ account: a.name, recover: e.target.checked })}
        />
        Usage recovery <span className="muted">(a dead usage read may spend one real turn to fix itself)</span>
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={a.cycle.on}
          disabled={!!off}
          title={off}
          onChange={(e) => save.mutate({ account: a.name, cycle: e.target.checked })}
        />
        Ends this month
      </label>
      <label>
        Last day <span className="muted">(YYYY-MM-DD; blank means the next monthly anniversary)</span>
        <input
          type="date"
          value={end}
          disabled={!!off}
          title={off}
          onChange={(e) => setEnd(e.target.value)}
          onBlur={() =>
            end !== (a.cycle.end_is_default ? '' : (a.cycle.end ?? '')) && save.mutate({ account: a.name, cycle_end: end || null })
          }
        />
      </label>
      {pending ? (
        <p className="muted" role="status">
          <i>○ saving…</i>
        </p>
      ) : null}
      {off ? <p className="muted">Locked: {off}.</p> : null}
    </form>
  );
}
