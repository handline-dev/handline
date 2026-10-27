// Two items of the chat strip that do more than show a value: the branch, which copies its name, and the context's maximum, which the
// context menu changes.
import { useEffect, useState } from 'react';
import type { AgentDetail } from '@handline/client';
import { useServes, useWrite } from '@handline/app-data';
import { Icon, MenuItem, MenuLabel, MenuSeparator, STRIP_ITEM } from '#src/ui/index.ts';

const COPIED_MS = 1500;

/** The branch name; its copy icon shows on hover or focus, and a click copies the name and says Copied for a moment. */
export function BranchChip({ branch }: { branch: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(t);
  }, [copied]);
  return (
    <button
      type="button"
      className={`group inline-flex ${STRIP_ITEM} min-w-0 items-center gap-1 rounded-ctl border-0 bg-transparent text-inherit tint focus-visible:bg-raised`}
      title={`Branch ${branch}: click to copy`}
      aria-label={copied ? 'Copied' : `Copy branch ${branch}`}
      onClick={() => void navigator.clipboard?.writeText(branch).then(() => setCopied(true))}
    >
      <Icon name="worktrees" size={13} aria-hidden />
      <span className="truncate">{branch}</span>
      {copied ? (
        <span className="flex-none text-ok">Copied</span>
      ) : (
        <Icon name="copy" size={12} aria-hidden className="flex-none opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100" />
      )}
    </button>
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
