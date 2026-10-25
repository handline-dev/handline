// The model and account of an agent as two chips that open a menu and apply at once: agent.set for the model, agent.move for the account.
// A model change while the agent works is kept by the daemon until the work ends; the chip says so until the live model matches.
import { MODELS, toneOf } from '@handline/app-core';
import type { AgentDetail } from '@handline/client';
import { EFFORTS, useAccounts, useServes, useVerbMark, useWrite } from '@handline/app-data';
import { Icon, MenuButton, MenuItem, MenuLabel, MenuSeparator } from '#src/ui/index.ts';

const CHIP = 'h-5 gap-1 px-1.5 text-hint text-ink-2';

export function ModelMenu({ detail }: { detail: AgentDetail }) {
  const set = useWrite('agent.set', detail.name);
  const { served } = useServes('agent.set');
  const chosen = detail.settings?.model;
  const live = detail.model;
  const waits = !!chosen && !!live && chosen !== live;
  const models = [...new Set([...MODELS, ...(chosen ? [chosen] : []), ...(live ? [live] : [])])];
  if (!live && !chosen) return null;
  const shown = chosen ?? live;
  const effort = detail.settings?.effort ?? detail.effort;
  const label = `Model ${shown}${effort ? `, ${effort}` : ''}${waits ? ' (applies when current work ends)' : ''}`;
  return (
    <MenuButton
      className={CHIP}
      label={label}
      name="Model"
      align="start"
      disabled={!served}
      trigger={
        <>
          <Icon name="agent" size={13} aria-hidden />
          {shown}
          {effort ? <span className="muted"> · {effort}</span> : null}
          {waits ? <span className="text-wait">applies when current work ends</span> : null}
        </>
      }
    >
      {models.map((m) => (
        <MenuItem
          key={m}
          icon={m === shown ? 'markRead' : undefined}
          onSelect={() => m !== shown && set.mutate({ agent: detail.name, values: { model: m } })}
        >
          {m}
        </MenuItem>
      ))}
      <MenuSeparator />
      <MenuLabel>Effort</MenuLabel>
      {EFFORTS.map((e) => (
        <MenuItem
          key={e}
          icon={e === effort ? 'markRead' : undefined}
          onSelect={() => e !== effort && set.mutate({ agent: detail.name, values: { effort: e } })}
        >
          {e}
        </MenuItem>
      ))}
    </MenuButton>
  );
}

export function AccountMenu({ detail }: { detail: AgentDetail }) {
  const move = useWrite('agent.move', detail.name);
  const { served } = useServes('agent.move');
  const all = useAccounts().data?.accounts ?? [];
  const listed = all.map((a) => a.name);
  const left = (name: string): number | null => {
    const w = all.find((a) => a.name === name)?.five_hour;
    return w ? 100 - Math.min(100, Math.max(0, Math.round(w.used_pct))) : null;
  };
  const moving = useVerbMark(detail.name) === 'moving…';
  const accounts = [...new Set([...listed, ...(detail.account ? [detail.account] : [])])];
  if (!detail.account) return null;
  return (
    <MenuButton
      className={CHIP}
      label={`Account ${detail.account}`}
      name="Account"
      align="start"
      disabled={!served || moving}
      trigger={
        <>
          <Icon name="accounts" size={13} aria-hidden />
          {detail.account}
          {moving ? <span className="muted">moving…</span> : null}
        </>
      }
    >
      {accounts.map((a) => (
        <MenuItem
          key={a}
          icon={a === detail.account ? 'markRead' : undefined}
          onSelect={() => a !== detail.account && move.mutate({ agent: detail.name, account: a })}
        >
          <span className="acct-name flex-1">{a}</span>
          <Room left={left(a)} />
        </MenuItem>
      ))}
    </MenuButton>
  );
}

const FILL = { ok: 'bg-ok', warn: 'bg-wait', bad: 'bg-bad' } as const;

/** The 5 h window's room an account has left, as a short bar and the words, the same reading the footer gauge shows. */
function Room({ left }: { left: number | null }) {
  if (left === null) return <span className="muted text-label">no reading</span>;
  return (
    <span className="room ml-3 inline-flex items-center gap-2 text-label text-ink-2" data-left={left}>
      <span
        className="relative h-2 w-14 overflow-hidden rounded-chip bg-raised"
        role="meter"
        aria-label="5 h room left"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={left}
      >
        <span className={`block h-full ${FILL[toneOf(100 - left)]}`} style={{ width: `${left}%` }} />
      </span>
      <span className="font-mono tabular-nums">{left}% left</span>
    </span>
  );
}
