// Two items of the chat strip that do more than show a value: the branch, which copies its name, and the context's maximum, which the
// context menu changes.
import { useEffect, useState } from 'react';
import type { AgentDetail } from '@handline/client';
import { VERBS } from '@handline/app-core';
import { useServes, useVerbRun, useWrite } from '@handline/app-data';
import { useServed } from '#src/AgentVerbs.tsx';
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
const CONTEXT_K = [100, 200, 300, 500, 1000];

/** The context menu's chooser: sets the agent's context maximum (agent.set context_k), the one now in force ticked. */
export function ContextMax({ detail, limit }: { detail: AgentDetail; limit: number | null | undefined }) {
  const set = useWrite('agent.set', detail.name);
  const { served } = useServes('agent.set');
  const chosen = Number(detail.settings?.context_k) || (limit ? Math.round(limit / 1000) : null);
  if (!served) return null;
  const options = [...new Set([...CONTEXT_K, ...(chosen ? [chosen] : [])])].sort((a, b) => a - b);
  return (
    <>
      <MenuSeparator />
      <MenuLabel>Maximum</MenuLabel>
      {options.map((k) => (
        <MenuItem
          key={k}
          icon={k === chosen ? 'markRead' : undefined}
          onSelect={() => k !== chosen && set.mutate({ agent: detail.name, values: { context_k: String(k) } })}
        >
          {k >= 1000 ? `${k / 1000}M` : `${k}k`} tokens
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
