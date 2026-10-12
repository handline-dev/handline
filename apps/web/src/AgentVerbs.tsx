// The agent verbs of the ⋯ menu beyond stopping: wake, restart (resuming or fresh), close, park, fold, move account and clear stall.
// Each is a decided write: the row shows the verb's gerund until the agents topic changes it. The ones in the bridge's CONFIRM ask first,
// in a sheet under the row, and the sheet checks the row again on Confirm: a row that changed sends nothing.
import { useEffect, useState } from 'react';
import { askOf, VERBS, verbsOf, type Verb } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useSelector } from '@handline/app-react';
import { useAccounts, useAgentDetail, useAgents, classify, sentence, useVerbRun } from '@handline/app-data';
import { Button, Sheet, SheetActions } from './ui/index.ts';

/** Whether the daemon lists the verb's method; a verb it does not list is left out of the menu. */
export function useServed(): (v: Verb) => boolean {
  const served = useSelector((s) => s.served);
  return (v) => served.has(v.method);
}

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
  const [refused, setRefused] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [onClose]);
  const move = verb.id === 'move';
  const structured = d?.structured === true;
  const interruptFirst = structured && verb.id === 'close' && (a.state === 'running' || a.state === 'waiting');
  const go = (v: Verb, acct?: string) => {
    setBusy(true);
    setRefused(null);
    run(v, acct, {
      onSuccess: onClose,
      onError: (e) => {
        setBusy(false);
        setRefused(sentence(classify(e)));
      },
    });
  };
  const confirm = () => {
    if (d && !verbsOf(a, d).some((v) => v.id === verb.id)) return setChanged(true); // the row changed while the sheet was open
    go(verb, account);
  };
  if (changed)
    return (
      <Sheet role="status">
        <span>It changed while you looked.</span>
        <Button variant="outline" label="Close" className="max-sm:min-h-12 max-sm:flex-1" onClick={onClose} />
      </Sheet>
    );
  return (
    <Sheet role="alertdialog" aria-label={`${verb.label} ${a.name}`}>
      <strong>
        {verb.label} {a.name}?
      </strong>
      {askOf(verb, { structured }) ? <span className="muted">{askOf(verb, { structured })}</span> : null}
      {refused ? <span role="alert">{refused}</span> : null}
      {interruptFirst ? <span className="muted">{a.name} is mid-turn. Interrupt ends the turn and keeps the session.</span> : null}
      {move ? (
        <select aria-label="Account" className="max-sm:min-h-12" value={account} onChange={(e) => setAccount(e.target.value)}>
          <option value="">choose an account</option>
          {accounts.map((x) => (
            <option key={x} value={x}>
              {x}
            </option>
          ))}
        </select>
      ) : null}
      <SheetActions>
        <Button
          variant="solid"
          label={move ? 'Move' : 'Confirm'}
          className="max-sm:min-h-12 max-sm:flex-1"
          autoFocus
          disabled={busy || (move && !account)}
          onClick={confirm}
        />
        {interruptFirst ? (
          <Button
            variant="outline"
            label="Interrupt first"
            className="max-sm:min-h-12 max-sm:flex-1"
            disabled={busy}
            onClick={() => go(VERBS.interrupt)}
          />
        ) : null}
        <Button variant="outline" label="Cancel" className="max-sm:min-h-12 max-sm:flex-1" onClick={onClose} />
      </SheetActions>
    </Sheet>
  );
}
