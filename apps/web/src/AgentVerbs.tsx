// The agent verbs of the ⋯ menu beyond stopping: wake, restart (resuming or fresh), close, park, fold, move account and clear stall.
// Each is a decided write: the row shows the verb's gerund until the agents topic changes it. The ones in the bridge's CONFIRM ask first,
// in a sheet under the row, and the sheet checks the row again on Confirm: a row that changed sends nothing.
import { useEffect, useRef, useState } from 'react';
import { verbsOf, type Verb } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useSelector } from '@handline/app-react';
import { useAccounts, useAgentDetail, useAgents, useVerbRun } from '@handline/app-data';

/** Whether the daemon lists the verb's method; one it does not list shows disabled with the reason. */
export function useServed(): (v: Verb) => boolean {
  const served = useSelector((s) => s.served);
  return (v) => served.has(v.method);
}
export const unserved = (v: Verb) => `This daemon does not serve ${v.method}.`;

/** The inline sheet under a row: the verb and agent, one sentence, Confirm and Cancel; Move account asks for the account instead. */
export function VerbSheet({ a, verb, onClose }: { a: AgentView; verb: Verb; onClose: () => void }) {
  const d = useAgentDetail(a.name).data;
  // The daemon's accounts when it lists them (an unused account is often the one to move to); else the ones agents already use.
  const listed = useAccounts().data?.accounts.map((x) => x.name);
  const used = (useAgents().data?.agents ?? []).map((x) => x.account);
  const accounts = [...new Set(listed ?? used)].filter((x): x is string => !!x && x !== a.account);
  const run = useVerbRun(a.name);
  const [account, setAccount] = useState('');
  const [changed, setChanged] = useState(false);
  const ok = useRef<HTMLButtonElement>(null);
  useEffect(() => ok.current?.focus(), []);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [onClose]);
  const move = verb.id === 'move';
  const confirm = () => {
    if (d && !verbsOf(a, d).some((v) => v.id === verb.id)) return setChanged(true); // the row changed while the sheet was open
    run(verb, account);
    onClose();
  };
  if (changed)
    return (
      <div className="verb-sheet" role="status">
        <span>It changed while you looked.</span>
        <button className="btn" onClick={onClose}>
          Close
        </button>
      </div>
    );
  return (
    <div className="verb-sheet" role="alertdialog" aria-label={`${verb.label} ${a.name}`}>
      <strong>
        {verb.label} {a.name}?
      </strong>
      {verb.ask ? <span className="muted">{verb.ask}</span> : null}
      {move ? (
        <select aria-label="Account" value={account} onChange={(e) => setAccount(e.target.value)}>
          <option value="">choose an account</option>
          {accounts.map((x) => (
            <option key={x} value={x}>
              {x}
            </option>
          ))}
        </select>
      ) : null}
      <span className="verb-sheet-actions">
        <button ref={ok} className="btn primary" disabled={move && !account} onClick={confirm}>
          {move ? 'Move' : 'Confirm'}
        </button>
        <button className="btn" onClick={onClose}>
          Cancel
        </button>
      </span>
    </div>
  );
}
