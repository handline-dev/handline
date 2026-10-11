// The HR tab: the proposals agents raised. Approve, reject and withdraw are decided writes (the card changes when the hr topic or a refetch
// says so); only the founder sees them, and a phone shows the cards read-only.
import { useEffect, useRef, useState } from 'react';
import { askedLines, cutError, diffLines, kindOf, isWaiting, ordered, stateWord, textSafe, waitingCount } from '@handline/app-core';
import type { Proposal } from '@handline/client';
import { useOnce, useStore } from '@handline/app-react';
import { classify, sentence, useAgents, useHr, usePending, useWrite } from '@handline/app-data';
import { Button, Empty, Icon, State, type StateKind } from './ui/index.ts';
import { COMPOSE, LINES } from './chatStyle.ts';
import { goSettings } from './route.ts';
import { HR_ACTIONS, HR_KV, META, META_ITEM } from './teamStyle.ts';
import { View } from './View.tsx';
import { FNOTE_BAD, FNOTE_WARN, SHEAD, VERB_SHEET, VERB_SHEET_ACTIONS, VERB_SHEET_DANGER } from './sheetStyle.ts';

const useNarrow = (): boolean => {
  const query = '(max-width: 640px)';
  const [on, setOn] = useState(() => (typeof matchMedia === 'function' ? matchMedia(query).matches : false));
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const m = matchMedia(query);
    const f = () => setOn(m.matches);
    m.addEventListener('change', f);
    return () => m.removeEventListener('change', f);
  }, []);
  return on;
};

/** A proposal's state as the shared state glyph: a shape per state, so the word's colour is never the only sign. */
const STATE_KIND: Record<string, StateKind> = {
  open: 'needs',
  apply_failed: 'blocked',
  approved: 'done',
  rejected: 'rejected',
  withdrawn: 'idle',
};

export function HrTab({ focus }: { focus: number | null }) {
  const q = useHr();
  const store = useStore();
  const founder = store.client.hello?.role === 'founder';
  const narrow = useNarrow();
  const decide = useWrite('hr.decide');
  const [all, setAll] = useState<'ask' | 'reject' | string | null>(null);
  const [why, setWhy] = useState('');
  const [going, once] = useOnce();
  const list = q.data?.proposals ?? [];
  const { waiting, recent } = ordered(list);
  const decider = founder && !narrow;
  const approveAll = () =>
    once(async () => {
      const todo = waiting;
      let ok = 0;
      for (const p of todo) {
        try {
          await decide.mutateAsync({ proposal: p.id, decision: 'approve', subject: p.subject });
          ok++;
        } catch {
          // the card shows its own refusal; the others go on
        }
      }
      setAll(ok === todo.length ? null : `${ok} of ${todo.length} approved`);
      void q.refetch();
    });
  const rejectAll = () =>
    once(async () => {
      const todo = waiting;
      const note = why.trim();
      let ok = 0;
      for (const p of todo) {
        try {
          await decide.mutateAsync({ proposal: p.id, decision: 'reject', note, subject: p.subject });
          ok++;
        } catch {
          // the card shows its own refusal; the others go on
        }
      }
      setWhy('');
      setAll(ok === todo.length ? null : `${ok} of ${todo.length} rejected`);
      void q.refetch();
    });
  return (
    <View q={q} served shape="inbox" what="HR proposals" empty={false}>
      <div className="hr mx-auto w-full max-w-[760px]">
        <div className="hr-head flex items-center justify-between py-2">
          <strong>HR{waitingCount(list) ? ` · ${waitingCount(list)} waiting` : ''}</strong>
          {decider && waiting.length > 1 && all !== 'ask' && all !== 'reject' ? (
            <span className={VERB_SHEET_ACTIONS}>
              <button className="btn" onClick={() => setAll('ask')}>
                Approve all
              </button>
              <button className="btn" onClick={() => setAll('reject')}>
                Reject all
              </button>
            </span>
          ) : null}
        </div>
        {all === 'ask' ? (
          <div className={VERB_SHEET} role="alertdialog" aria-label="Approve all">
            <strong>Approve all {waiting.length}?</strong>
            <span className="muted">Each is applied as the card shows it; one that fails stays waiting.</span>
            <span className={VERB_SHEET_ACTIONS}>
              <button className="btn primary" disabled={going} onClick={() => void approveAll()}>
                Approve all
              </button>
              <button className="btn" disabled={going} onClick={() => setAll(null)}>
                Cancel
              </button>
            </span>
          </div>
        ) : all === 'reject' ? (
          <div className={VERB_SHEET} role="alertdialog" aria-label="Reject all">
            <strong>Reject all {waiting.length}?</strong>
            <input aria-label="Reason" placeholder="Reason" value={why} onChange={(e) => setWhy(e.target.value)} />
            <span className={VERB_SHEET_ACTIONS}>
              <button className="btn primary" disabled={going || !why.trim()} onClick={() => void rejectAll()}>
                Reject all
              </button>
              <button className="btn" disabled={going} onClick={() => setAll(null)}>
                Cancel
              </button>
            </span>
          </div>
        ) : all ? (
          <p className={FNOTE_WARN} role="status">
            {all}
          </p>
        ) : null}
        {list.length ? null : <Empty icon="inbox" name="Nothing to decide" hint="Roster and role changes agents propose land here." />}
        {waiting.map((p) => (
          <Card key={p.id} p={p} decider={decider} narrow={narrow} focus={focus === p.id} refresh={() => void q.refetch()} />
        ))}
        {recent.length ? <h3 className={SHEAD}>Recent</h3> : null}
        {recent.map((p) => (
          <Card key={p.id} p={p} decider={false} narrow={narrow} focus={focus === p.id} refresh={() => void q.refetch()} />
        ))}
      </div>
    </View>
  );
}

function Card({
  p,
  decider,
  narrow,
  focus,
  refresh,
}: {
  p: Proposal;
  decider: boolean;
  narrow: boolean;
  focus: boolean;
  refresh: () => void;
}) {
  const decide = useWrite('hr.decide', String(p.id));
  const withdraw = useWrite('hr.withdraw', String(p.id));
  const deciding = usePending('hr.decide').some((v) => v.proposal === p.id);
  const withdrawing = usePending('hr.withdraw').some((v) => v.proposal === p.id);
  const busy = deciding || withdrawing;
  // A bundle's agent is "<N> changes" and a hire's does not exist yet: only an agent on the roster has settings to open.
  const onRoster = !!useAgents().data?.agents.some((a) => a.name === p.agent);
  const [note, setNote] = useState('');
  const [reason, setReason] = useState<string | null>(null);
  const [ask, setAsk] = useState(false);
  const [mark, setMark] = useState<string | null>(null);
  const [showDiff, setShowDiff] = useState(!narrow);
  const el = useRef<HTMLElement>(null);
  const first = useRef(p.subject);
  useEffect(() => {
    if (focus) el.current?.scrollIntoView?.({ block: 'center' });
  }, [focus]);
  useEffect(() => {
    // the text the card showed changed under it (a refusal brought the new proposal, or an event did): say so
    if (first.current !== p.subject) setNote('This proposal changed');
  }, [p.subject]);
  const waiting = isWaiting(p);
  const state = stateWord(p);
  const diff = diffLines(p);
  const sent = (call: Promise<unknown>, cause: string) => {
    setNote('');
    setMark(cause);
    call
      .then(refresh, (e) => {
        const gate = (e as { gate?: string }).gate;
        if (gate === 'proposal_changed') setNote('This proposal changed');
        else if (gate === 'proposal_settled') setNote('settled');
        else setNote(sentence(classify(e)));
      })
      .finally(() => setMark(null));
  };
  const approve = () => sent(decide.mutateAsync({ proposal: p.id, decision: 'approve', subject: p.subject }), 'approve');
  const reject = () => {
    if (!reason?.trim()) return;
    const why = reason.trim();
    setReason(null);
    sent(decide.mutateAsync({ proposal: p.id, decision: 'reject', note: why, subject: p.subject }), 'reject');
  };
  return (
    <article
      className={`hr-card mb-3 flex flex-col gap-2 rounded-card border border-line bg-surface p-3${focus ? ' focus outline-2 outline-focus' : ''}`}
      data-proposal={p.id}
      ref={el}
    >
      <header className="hr-top flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
        <span className="hr-title inline-flex min-w-0 items-center gap-1.5 text-title font-semibold [overflow-wrap:anywhere]">
          <Icon name="team" size={16} aria-hidden="true" /> {textSafe(p.title)}
        </span>
        <span className={`hr-state ${state.tone} inline-flex items-center gap-1.5 text-label text-ink-2`}>
          <State kind={STATE_KIND[p.state] ?? 'idle'} label={state.word} />
          {state.word}
        </span>
      </header>
      <p className={`hr-meta ${META}`}>
        {p.requested_by ? (
          <span className={META_ITEM} title="Asked by">
            <Icon name="agent" size={14} label="Asked by" /> {textSafe(p.requested_by)}
          </span>
        ) : null}
        <span className={META_ITEM} title="Raised">
          <Icon name="schedules" size={14} label="Raised" /> {new Date(p.created).toLocaleString()}
        </span>
        {kindOf(p) ? <span className={`hr-kind ${META_ITEM} rounded-chip border border-line px-1.5`}>{kindOf(p)}</span> : null}
      </p>
      {askedLines(p).map(([k, v]) => (
        <div key={k} className={HR_KV}>
          <span className="muted">{k}</span>
          <span>{v}</span>
        </div>
      ))}
      {p.reason ? (
        <div className={HR_KV}>
          <span className="muted">why</span>
          <span>{textSafe(p.reason)}</span>
        </div>
      ) : null}
      {narrow ? (
        <Button variant="outline" size="sm" label="Diff" icon={showDiff ? 'less' : 'more'} onClick={() => setShowDiff(!showDiff)}>
          {showDiff ? 'Hide diff' : `Show diff (${diff.length} change${diff.length === 1 ? '' : 's'})`}
        </Button>
      ) : null}
      {showDiff ? (
        <pre className={`${LINES} hr-diff max-h-[20lh] overflow-auto`} aria-label="What approving does">
          {diff.map((l, i) => (
            <div key={i} className={l.mark === '-' ? 'del text-bad' : l.mark === '+' ? 'add text-ok' : ''}>
              {l.text}
            </div>
          ))}
        </pre>
      ) : null}
      {p.state === 'apply_failed' ? (
        <p className={FNOTE_BAD}>{p.error ? cutError(textSafe(p.error)) : 'the change was not applied'}</p>
      ) : null}
      {note === 'settled' ? (
        <p className="muted">already {p.state}</p>
      ) : note ? (
        <p className="stale" role="status">
          {note}
        </p>
      ) : null}
      {waiting && narrow ? <p className="muted">Decide on a computer.</p> : null}
      {waiting && decider ? (
        <div className={HR_ACTIONS}>
          <Button
            variant="solid"
            icon="markRead"
            label={p.state === 'apply_failed' ? 'Retry' : 'Approve'}
            keys="a"
            disabled={busy || !!mark}
            onClick={approve}
          >
            {mark === 'approve' || busy ? '…' : p.state === 'apply_failed' ? 'Retry' : 'Approve'}
          </Button>
          <Button variant="outline" icon="close" label="Reject" keys="r" disabled={busy || !!mark} onClick={() => setReason('')}>
            Reject
          </Button>
          {onRoster ? (
            <Button variant="ghost" icon="settings" label="Edit settings" onClick={() => goSettings(p.agent)}>
              Edit settings
            </Button>
          ) : null}
          <Button variant="ghost" icon="discard" label="Withdraw" keys="w" disabled={busy || !!mark} onClick={() => setAsk(true)}>
            Withdraw
          </Button>
        </div>
      ) : null}
      {reason !== null ? (
        <form
          className={HR_ACTIONS}
          onSubmit={(e) => {
            e.preventDefault();
            reject();
          }}
        >
          <input
            type="text"
            className={COMPOSE}
            autoFocus
            aria-label="Why reject"
            placeholder="why - Enter rejects, Esc cancels"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => e.key === 'Escape' && setReason(null)}
          />
        </form>
      ) : null}
      {ask ? (
        <div className={VERB_SHEET_DANGER} role="alertdialog" aria-label="Withdraw">
          <strong>Withdraw {textSafe(p.title)}</strong>
          <span className="muted">Takes the proposal back; whoever raised it can raise it again.</span>
          <span className={VERB_SHEET_ACTIONS}>
            <button
              className="btn primary"
              onClick={() => {
                setAsk(false);
                sent(withdraw.mutateAsync({ proposal: p.id }), 'withdraw');
              }}
            >
              Withdraw
            </button>
            <button className="btn" onClick={() => setAsk(false)}>
              Cancel
            </button>
          </span>
        </div>
      ) : null}
    </article>
  );
}
