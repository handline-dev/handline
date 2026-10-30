// The model and account of an agent as two chips that open a menu and apply at once: agent.set for the model, agent.move for the account.
// A model change while the agent works is kept by the daemon until the work ends; the chip says so until the live model matches.
import { MODELS, VERBS, toneOf } from '@handline/app-core';
import type { AgentDetail } from '@handline/client';
import { EFFORTS, useAccounts, useServes, useVerbMark, useVerbRun, useWrite } from '@handline/app-data';
import { useServed } from '#src/AgentVerbs.tsx';
import { Button } from '#src/ds/Button.tsx';
import { Icon, MenuButton, MenuItem, MenuLabel, MenuSeparator, STRIP_ITEM } from '#src/ui/index.ts';

const CHIP = `${STRIP_ITEM} gap-1 text-xs text-ink-2`;

export function ModelMenu({ detail }: { detail: AgentDetail }) {
  const set = useWrite('agent.set', detail.name);
  const { served } = useServes('agent.set');
  const chosen = detail.settings?.model;
  const live = detail.model;
  const effort = detail.settings?.effort ?? detail.effort;
  const waits =
    (!!chosen && !!live && chosen !== live) || (!!detail.settings?.effort && !!detail.effort && detail.settings.effort !== detail.effort);
  const run = useVerbRun(detail.name);
  const canApply = useServed()(VERBS.restart) && detail.session_open;
  const models = [...new Set([...MODELS, ...(chosen ? [chosen] : []), ...(live ? [live] : [])])];
  if (!live && !chosen) return null;
  const shown = chosen ?? live;
  const label = `Model ${shown}${effort ? `, ${effort}` : ''}${waits ? ' (applies from the next session)' : ''}`;
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
          {waits ? <span className="text-wait">applies from the next session</span> : null}
          <Icon name="chevron" size={12} aria-hidden />
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
      <div className="px-2 pb-1.5 pt-0.5 text-xs text-ink-2">
        <div className="flex justify-between gap-4 py-0.5">
          <span className="text-ink-3">Effort</span>
          <span className="text-ink">{effort ?? 'default'}</span>
        </div>
        <input
          type="range"
          className="w-full"
          aria-label="Effort"
          aria-valuetext={effort ?? undefined}
          min={0}
          max={EFFORTS.length - 1}
          step={1}
          value={Math.max(0, (EFFORTS as readonly string[]).indexOf(effort ?? ''))}
          onChange={(e) => set.mutate({ agent: detail.name, values: { effort: EFFORTS[Number(e.target.value)]! } })}
        />
        <div className="flex justify-between text-ink-3" aria-hidden>
          <span>{EFFORTS[0]}</span>
          <span>{EFFORTS[EFFORTS.length - 1]}</span>
        </div>
        <p className="m-0 pt-1 text-ink-3">Applies from the next session.</p>
        {canApply ? (
          <div className="pt-1">
            <Button size="s" label="Apply now" onClick={() => run(VERBS.restart)} />
          </div>
        ) : null}
      </div>
    </MenuButton>
  );
}

export function AccountMenu({ detail }: { detail: AgentDetail }) {
  const move = useWrite('agent.move', detail.name);
  const { served } = useServes('agent.move');
  const all = useAccounts().data?.accounts ?? [];
  const mine = all.find((a) => a.name === detail.account);
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
      {mine ? (
        <>
          <div className="px-2 pb-1 pt-0.5 text-sm text-ink-2">
            {[
              mine.five_hour ? `5-hour ${Math.round(mine.five_hour.used_pct)}% used` : null,
              mine.weekly ? `week ${Math.round(mine.weekly.used_pct)}% used` : null,
            ]
              .filter(Boolean)
              .join(' · ') || 'No usage reading'}
          </div>
          <MenuSeparator />
          <MenuLabel>Change account</MenuLabel>
        </>
      ) : null}
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
  if (left === null) return <span className="muted text-sm">no reading</span>;
  return (
    <span className="room ml-3 inline-flex items-center gap-2 text-sm text-ink-2" data-left={left}>
      <span
        className="relative h-2 w-14 overflow-hidden rounded-sm bg-raised"
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
