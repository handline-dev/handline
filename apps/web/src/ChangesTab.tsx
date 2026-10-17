// What an agent changed in its worktree: the changed files on the left, the diff of the picked one (or all) on the right.
import { useState } from 'react';
import type { AgentDetail } from '@handline/client';
import { useServes, useWorktreeDiff, useWorktrees } from '@handline/app-data';
import { Button, Icon, MenuButton, MenuItem } from './ui/index.ts';
import { shell } from './shell.ts';
import { View } from './View.tsx';

const STATUS: Record<string, string> = { M: 'Modified', A: 'Added', D: 'Deleted', R: 'Renamed', '?': 'Untracked' };

/** The line's look in a unified diff: added and removed lines take the ok and bad hues, a hunk head the muted one. */
const lineClass = (l: string): string =>
  l.startsWith('+') && !l.startsWith('+++')
    ? 'add text-ok'
    : l.startsWith('-') && !l.startsWith('---')
      ? 'del text-bad'
      : l.startsWith('@@')
        ? 'text-ink-2'
        : '';

export function ChangesTab({ agent, detail }: { agent: string; detail: AgentDetail }) {
  const path = detail.worktree?.path ?? '';
  const q = useWorktrees();
  const { served } = useServes('worktrees.list');
  const row = q.data?.worktrees.find((w) => w.path === path);
  const changed = row?.changed ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const file = changed.some((c) => c.path === picked) ? picked : null;
  const open = shell('openPath');
  const diffQ = useWorktreeDiff(path, file ?? undefined);
  const copy = (text: string) => void navigator.clipboard?.writeText(text);
  return (
    <section className="changes-tab flex h-full min-h-0 flex-col" aria-label={`Changes of ${agent}`}>
      <div className="flex flex-none items-center gap-2 border-b border-line px-3 py-1.5">
        <Icon name="changes" size={14} aria-hidden />
        <span className="mono min-w-0 flex-1 truncate text-meta text-ink-2" title={path}>
          {detail.worktree?.branch ?? ''}
        </span>
        <Button icon="refresh" size="sm" variant="outline" label="Reload" onClick={() => void Promise.all([q.refetch(), diffQ.refetch()])}>
          Reload
        </Button>
        <MenuButton icon="open" size="sm" variant="outline" label="Open outside" name="Open outside" trigger="Open outside">
          {open ? (
            <MenuItem icon="open" onSelect={() => void open.openPath!({ path, agent })}>
              Show in Finder
            </MenuItem>
          ) : null}
          <MenuItem icon="reference" onSelect={() => copy(path)}>
            Copy path
          </MenuItem>
        </MenuButton>
      </div>
      <View
        q={q}
        served={served}
        shape="worktrees"
        what="the changes"
        empty={!detail.worktree ? `${agent} has no worktree.` : !changed.length && 'Nothing changed in the worktree.'}
      >
        <div className="flex min-h-0 flex-1">
          <ul className="changed m-0 w-[280px] flex-none list-none overflow-auto border-r border-line p-1" aria-label="Changed files">
            <li>
              <button
                className={`row-all flex w-full items-center gap-2 rounded-card border-0 px-2 py-1 text-left text-body ${file === null ? 'bg-raised' : 'bg-transparent'}`}
                onClick={() => setPicked(null)}
                aria-pressed={file === null}
              >
                All {changed.length} files
              </button>
            </li>
            {changed.map((c) => (
              <li key={c.path}>
                <button
                  className={`flex w-full items-center gap-2 rounded-card border-0 px-2 py-1 text-left text-body ${file === c.path ? 'bg-raised' : 'bg-transparent'}`}
                  onClick={() => setPicked(c.path)}
                  aria-pressed={file === c.path}
                  title={`${STATUS[c.status] ?? c.status}: ${c.path}`}
                >
                  <span className="status flex-none text-hint text-ink-2">{STATUS[c.status] ?? c.status}</span>
                  <span className="min-w-0 flex-1 truncate">{c.path}</span>
                  <span className="flex-none text-hint text-ok">+{c.added}</span>
                  <span className="flex-none text-hint text-bad">−{c.deleted}</span>
                </button>
              </li>
            ))}
          </ul>
          <Diff q={diffQ} untracked={file === null && changed.some((c) => c.status === '?')} />
        </div>
      </View>
    </section>
  );
}

function Diff({ q, untracked }: { q: ReturnType<typeof useWorktreeDiff>; untracked: boolean }) {
  const { served } = useServes('worktree.diff');
  const d = q.data;
  return (
    <div className="diff min-w-0 flex-1 overflow-auto p-2">
      <View q={q} served={served} shape="worktrees" what="the diff" empty={d && !d.diff && !untracked && 'No difference to show.'}>
        {d ? (
          <>
            {untracked && !d.diff.trim() ? <p className="muted">Untracked files: pick the file.</p> : null}
            <pre className="m-0 font-mono text-[12px] leading-5">
              {d.diff.split('\n').map((l, i) => (
                <div key={i} className={lineClass(l)}>
                  {l || ' '}
                </div>
              ))}
            </pre>
            {d.truncated ? <p className="muted">The diff is cut here; open the worktree to see the rest.</p> : null}
          </>
        ) : null}
      </View>
    </div>
  );
}
