// Two items of the chat strip that do more than show a value: the branch, which copies its name, and the context's maximum, which the
// context menu changes.
import { useEffect, useState } from 'react';
import type { AgentDetail } from '@handline/client';
import { VERBS } from '@handline/app-core';
import { useServes, useVerbRun, useWrite } from '@handline/app-data';
import { useServed } from '#src/AgentVerbs.tsx';
import { GaugeLine } from '#src/AgentGauges.tsx';
import { Icon, MenuButton, MenuItem, MenuLabel, MenuSeparator, STRIP_ITEM } from '#src/ui/index.ts';

const COPIED_MS = 1500;

type ChangedFile = { path: string; added: number; deleted: number };

/** The branch with the lines it adds and removes; it opens the branch name (a click copies it) and the files that changed, the biggest three first. */
export function BranchMenu({ branch, changed }: { branch: string; changed: ChangedFile[] }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(t);
  }, [copied]);
  const added = changed.reduce((n, f) => n + f.added, 0);
  const deleted = changed.reduce((n, f) => n + f.deleted, 0);
  const biggest = [...changed].sort((a, b) => b.added + b.deleted - (a.added + a.deleted)).slice(0, 3);
  const more = changed.length - biggest.length;
  return (
    <MenuButton
      className={`${STRIP_ITEM} min-w-0 gap-1 text-xs text-ink-2`}
      label={`Branch ${branch}, ${changed.length} changed files`}
      name="Branch"
      align="start"
      trigger={
        <>
          <Icon name="worktrees" size={13} aria-hidden />
          <span className="truncate">{branch}</span>
          {changed.length ? (
            <span className="flex-none font-mono tabular-nums">
              +{added} −{deleted}
            </span>
          ) : null}
        </>
      }
    >
      <MenuItem
        icon={copied ? 'markRead' : 'copy'}
        onSelect={(e) => (e.preventDefault(), void navigator.clipboard?.writeText(branch).then(() => setCopied(true)))}
      >
        <span className="truncate">{copied ? 'Copied' : branch}</span>
      </MenuItem>
      {changed.length ? (
        <>
          <MenuSeparator />
          <MenuLabel>
            {changed.length} {changed.length === 1 ? 'file' : 'files'} changed
          </MenuLabel>
          {biggest.map((f) => (
            <div key={f.path} className="flex items-center gap-3 px-2 py-0.5 text-sm text-ink-2">
              <span className="min-w-0 flex-1 truncate font-mono">{f.path}</span>
              <span className="flex-none font-mono tabular-nums">
                +{f.added} −{f.deleted}
              </span>
            </div>
          ))}
          {more > 0 ? (
            <div className="px-2 py-0.5 text-sm text-ink-3">
              {more} more {more === 1 ? 'file' : 'files'}
            </div>
          ) : null}
        </>
      ) : null}
    </MenuButton>
  );
}

/** The maximums offered for the context window, in thousands of tokens. */
const CONTEXT_K = [100, 150, 200, 300];

const tokens = (n: number): string => (n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));

/** The fresh-start point in tokens: the agent's context_k when it has one, else none. */
export const freshPoint = (detail: AgentDetail): number | null => (Number(detail.settings?.context_k) || 0) * 1000 || null;

/** The context window as one bar with a mark at the fresh-start point; the header says how much is used of that point, or that it is over. */
export function ContextBar({ detail, used, limit }: { detail: AgentDetail; used: number; limit: number | null | undefined }) {
  const point = freshPoint(detail);
  const over = point != null && used > point;
  const scale = Math.max(limit ?? 0, point ?? 0, used) || 1;
  return (
    <>
      <GaugeLine what="Used">{over ? `${tokens(used)} · over` : `${tokens(used)} of ${tokens(point ?? limit ?? used)}`}</GaugeLine>
      <div
        className="relative my-1 h-1.5 rounded-full border border-ink-2"
        role="img"
        aria-label={`${tokens(used)} used of ${tokens(scale)}`}
      >
        <div
          className={`h-full rounded-full ${over ? 'bg-wait' : 'bg-working'}`}
          style={{ width: `${Math.min(100, (used / scale) * 100)}%` }}
        />
        {point != null ? (
          <span
            className="absolute -top-0.5 h-2.5 w-0.5 rounded-full bg-ink-2"
            style={{ left: `${(point / scale) * 100}%` }}
            title={`Starts fresh at ${tokens(point)}`}
          />
        ) : null}
      </div>
      {over ? <p className="m-0 py-0.5 text-ink-3">Starts fresh with a handoff when this turn ends</p> : null}
    </>
  );
}

/** The context menu's chooser: sets the point where the session starts fresh (agent.set context_k), the one now in force ticked. */
export function ContextMax({ detail }: { detail: AgentDetail }) {
  const set = useWrite('agent.set', detail.name);
  const { served } = useServes('agent.set');
  const chosen = Number(detail.settings?.context_k) || null;
  if (!served) return null;
  const options = [...new Set([...CONTEXT_K, ...(chosen ? [chosen] : [])])].sort((a, b) => a - b);
  return (
    <>
      <MenuSeparator />
      <MenuLabel>Starts fresh at</MenuLabel>
      {options.map((k) => (
        <MenuItem
          key={k}
          icon={k === chosen ? 'markRead' : undefined}
          onSelect={() => k !== chosen && set.mutate({ agent: detail.name, values: { context_k: String(k) } })}
        >
          {k >= 1000 ? `${k / 1000}M` : `${k}k`}
        </MenuItem>
      ))}
    </>
  );
}

/** The one action of the context menu: start the session over with a handoff now. */
export function StartFresh({ detail }: { detail: AgentDetail }) {
  const run = useVerbRun(detail.name);
  const served = useServed();
  const verb = VERBS.restart_fresh;
  if (!detail.session_open || !served(verb)) return null;
  return (
    <>
      <MenuSeparator />
      <MenuItem icon="refresh" onSelect={() => run(verb)}>
        Start fresh now
      </MenuItem>
    </>
  );
}
