// Start or Resume and the ⋯ menu of the selected agent row.
import { useState } from 'react';
import { controlsOf, stopModesOf, asks, verbsOf, type Verb, type StopMode } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useStore } from '@handline/app-react';
import { useAgentDetail, useAgents, useVerbMark, useVerbRun, useWrite } from '@handline/app-data';
import { unserved, useServed } from './AgentVerbs.tsx';
import { Button, Icon, Popup, PROW } from './ui/index.ts';
import { goSettings } from './route.ts';

/** Start or Resume, and the ⋯ menu: the agent verbs the row's state allows, then the ways to stop. Which apply comes from the agent's detail. */
export function RowControls({
  agent: a,
  account,
  setAccount,
  onAsk,
}: {
  agent: AgentView;
  account: string;
  setAccount: (a: string) => void;
  onAsk: (v: Verb) => void;
}) {
  const accounts = [...new Set((useAgents().data?.agents ?? []).map((x) => x.account).filter((x): x is string => !!x))];
  const agent = a.name;
  const store = useStore();
  const d = useAgentDetail(agent).data;
  const [open, setOpen] = useState(false);
  const start = useWrite('session.start', agent);
  const resume = useWrite('session.wake', agent);
  const stopWrite = useWrite('session.stop', agent);
  const interrupt = useWrite('session.interrupt', agent);
  const run = useVerbRun(agent);
  const served = useServed();
  const mark = useVerbMark(agent);
  const busy = start.isPending || resume.isPending || stopWrite.isPending || interrupt.isPending || !!mark;
  if (!d) return null;
  const c = controlsOf(d, store.me);
  const verb = (v: Verb) => {
    setOpen(false);
    if (asks(v) || v.id === 'move') onAsk(v);
    else run(v);
  };
  const stop = (mode: StopMode) => {
    setOpen(false);
    if (
      mode === 'kill' &&
      !confirm(
        `${d.structured ? `End the session of ${agent}` : `Kill the terminal of ${agent}`}?${a.task ? ` It has work in flight: ${a.task.title}.` : ''}`,
      )
    )
      return;
    if (mode === 'interrupt') interrupt.mutate({ agent });
    else stopWrite.mutate({ agent, mode });
  };
  const verbs = verbsOf(a, d);
  return (
    <span className="row-actions relative flex flex-none items-center gap-1">
      {c.start ? (
        <Button
          variant="solid"
          label="Start"
          disabled={busy}
          onClick={() => start.mutate(d.structured ? { agent } : { agent, account: account || undefined })}
        >
          {start.isPending ? (
            'starting…'
          ) : (
            <>
              <Icon name="play" size={12} aria-hidden className="mr-1 inline-block align-[-2px]" /> Start
            </>
          )}
        </Button>
      ) : null}
      {c.resume ? (
        <Button variant="outline" label="Resume" disabled={busy} onClick={() => resume.mutate({ agent })}>
          {resume.isPending ? (
            'resuming…'
          ) : (
            <>
              <Icon name="retry" size={12} aria-hidden className="mr-1 inline-block align-[-2px]" /> Resume
            </>
          )}
        </Button>
      ) : null}
      <Button
        label={`More actions for ${agent}`}
        aria-label={`More actions for ${agent}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {mark ?? (stopWrite.isPending || interrupt.isPending ? 'stopping…' : <Icon name="menu" size={14} aria-hidden />)}
      </Button>
      <Popup open={open} onClose={() => setOpen(false)} title={`More actions for ${agent}`} align="right">
        {c.start && !d.structured ? (
          <select
            aria-label="Account for this start"
            title={`The daemon would pick: ${d.account_pick.account ?? 'none'} (${d.account_pick.reason})`}
            value={account}
            onChange={(e) => setAccount(e.target.value)}
          >
            <option value="">{`start on auto: ${d.account_pick.account ?? 'none'}`}</option>
            {accounts.map((x) => (
              <option key={x} value={x}>
                {x}
              </option>
            ))}
          </select>
        ) : null}
        {verbs.map((v) => (
          <button
            key={v.id}
            type="button"
            className={`${PROW} max-sm:min-h-12 disabled:cursor-not-allowed disabled:opacity-55`}
            disabled={busy || !served(v)}
            title={served(v) ? undefined : unserved(v)}
            onClick={() => verb(v)}
          >
            <strong>{v.label}</strong>
            {served(v) ? null : <span className="muted"> not served</span>}
          </button>
        ))}
        <button className={`${PROW} max-sm:min-h-12`} onClick={() => goSettings(agent)}>
          <strong>Settings</strong>
        </button>
        {c.stop
          ? stopModesOf(d)
              .filter((m) => m.mode !== 'close' && !(d.structured && m.mode === 'interrupt'))
              .map((m) => (
                <button
                  key={m.mode}
                  type="button"
                  className={`${PROW} max-sm:min-h-12 disabled:cursor-not-allowed disabled:opacity-55${m.mode === 'kill' ? ' [&>strong]:text-bad' : ''}`}
                  disabled={busy}
                  onClick={() => stop(m.mode)}
                >
                  <strong>{m.label}</strong>
                  <span className="muted"> {m.hint}</span>
                </button>
              ))
          : null}
      </Popup>
    </span>
  );
}
