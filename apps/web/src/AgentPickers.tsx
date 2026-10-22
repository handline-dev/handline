// The model and account of an agent as two chips that open a menu and apply at once: agent.set for the model, agent.move for the account.
// A model change while the agent works is kept by the daemon until the work ends; the chip says so until the live model matches.
import { MODELS } from '@handline/app-core';
import type { AgentDetail } from '@handline/client';
import { useAccounts, useServes, useVerbMark, useWrite } from '@handline/app-data';
import { Icon, MenuButton, MenuItem } from '#src/ui/index.ts';

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
  const label = `Model ${shown}${detail.effort ? `, ${detail.effort}` : ''}${waits ? ' (applies when current work ends)' : ''}`;
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
          {detail.effort ? <span className="muted"> · {detail.effort}</span> : null}
          {waits ? <span className="text-wait">applies when current work ends</span> : null}
        </>
      }
    >
      {models.map((m) => (
        <MenuItem key={m} icon={m === shown ? 'markRead' : undefined} onSelect={() => m !== shown && set.mutate({ agent: detail.name, values: { model: m } })}>
          {m}
        </MenuItem>
      ))}
    </MenuButton>
  );
}

export function AccountMenu({ detail }: { detail: AgentDetail }) {
  const move = useWrite('agent.move', detail.name);
  const { served } = useServes('agent.move');
  const listed = useAccounts().data?.accounts.map((a) => a.name) ?? [];
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
        <MenuItem key={a} icon={a === detail.account ? 'markRead' : undefined} onSelect={() => a !== detail.account && move.mutate({ agent: detail.name, account: a })}>
          {a}
        </MenuItem>
      ))}
    </MenuButton>
  );
}
