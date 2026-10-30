// The agent verbs of the ⋯ menu beyond stopping: wake, restart (resuming or fresh), close, park, fold, move account and clear stall.
// Each is a decided write: the row shows the verb's gerund until the agents topic changes it. The ones in the bridge's CONFIRM ask first,
// in a sheet under the row, and the sheet checks the row again on Confirm: a row that changed sends nothing.
import { useState } from 'react';
import { askOf, heldWords, VERBS, verbsOf, type HeldWorktree, type Verb, type WorktreeAnswer } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useSelector } from '@handline/app-react';
import { useAccounts, useAgentDetail, useAgents, classify, sentence, useRetireAgent, useVerbRun } from '@handline/app-data';
import { Button, Select, Sheet, SheetActions } from '#src/ui/index.ts';
import { useEscape } from '#src/useEscape.ts';

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
  useEscape(onClose);
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
        <Select
          label="Account"
          value={account}
          onChange={(e) => setAccount(e.target.value)}
          options={[{ value: '', label: 'choose an account' }, ...accounts.map((x) => ({ value: x, label: x }))]}
        />
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

/** The delete pop's three answers for a worktree that holds work: the safe one is filled, the one that loses files asks once more. */
const POP_ANSWERS: { answer: WorktreeAnswer; label: string }[] = [
  { answer: 'save', label: 'Save to a branch, then delete' },
  { answer: 'keep', label: 'Delete, keep the worktree' },
  { answer: 'force', label: 'Delete and lose the files' },
];

/** Delete agent: the inline sheet in danger style says what goes and what stays; a refusal (a running session, agents under it) shows as the daemon says it.
 * A worktree that holds work turns it into the three answers for that work, and the delete is sent again with the one picked. */
export function DeleteSheet({ a, onClose }: { a: AgentView; onClose: () => void }) {
  const retire = useRetireAgent(a.name);
  const [refused, setRefused] = useState<string | null>(null);
  const [held, setHeld] = useState<HeldWorktree[]>([]);
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);
  useEscape(onClose);
  const go = (worktree?: WorktreeAnswer) => {
    setBusy(true);
    setRefused(null);
    retire(
      {
        onSuccess: onClose,
        onError: (e) => {
          setBusy(false);
          const f = classify(e);
          if (f.kind === 'refused' && f.held?.length) setHeld(f.held);
          else setRefused(sentence(f));
        },
      },
      worktree,
    );
  };
  const alert = refused ? <span role="alert">{refused}</span> : null;
  if (held.length)
    return (
      <Sheet role="alertdialog" aria-label={`${a.name} has work in its worktree`} danger>
        <strong>{a.name} has work in its worktree</strong>
        <span className="muted">{`${a.name} has ${heldWords(held)}. Nothing is deleted until you pick.`}</span>
        {sure ? <span role="alert">Delete for good? Uncommitted files are lost.</span> : alert}
        <SheetActions>
          {sure ? (
            <>
              <Button variant="solid" label="Delete" className="max-sm:min-h-12 max-sm:flex-1" disabled={busy} onClick={() => go('force')} />
              <Button variant="outline" label="Cancel" autoFocus className="max-sm:min-h-12 max-sm:flex-1" onClick={() => setSure(false)} />
            </>
          ) : (
            <>
              {POP_ANSWERS.map((o, i) => (
                <Button
                  key={o.answer}
                  variant={i === 0 ? 'solid' : 'outline'}
                  label={o.label}
                  className="max-sm:min-h-12 max-sm:flex-1"
                  disabled={busy}
                  onClick={() => (o.answer === 'force' ? setSure(true) : go(o.answer))}
                />
              ))}
              <Button variant="outline" label="Cancel" autoFocus className="max-sm:min-h-12 max-sm:flex-1" onClick={onClose} />
            </>
          )}
        </SheetActions>
      </Sheet>
    );
  return (
    <Sheet role="alertdialog" aria-label={`Delete ${a.name}`} danger>
      <strong>Delete {a.name}?</strong>
      <span className="muted">
        Its team entry, schedule and keys go, and its folder is moved to the archive. Past messages, tasks and events stay. A worktree with
        nothing in it goes with its branch; one that holds work asks you first.
      </span>
      {alert}
      <SheetActions>
        <Button variant="solid" label="Delete" className="max-sm:min-h-12 max-sm:flex-1" disabled={busy} onClick={() => go()} />
        <Button variant="outline" label="Cancel" autoFocus className="max-sm:min-h-12 max-sm:flex-1" onClick={onClose} />
      </SheetActions>
    </Sheet>
  );
}
