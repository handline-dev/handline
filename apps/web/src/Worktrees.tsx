// The Worktrees page: every worktree the daemon knows, with repo and owner filters (client-side) and a search, and a side panel for one.
// A worktree is made by starting an agent; its status label is changed by a decided write (the row moves when the refetch shows it).
import { useEffect, useState } from 'react';
import { ago } from '@handline/app-core';
import type { Worktree } from '@handline/client';
import { classify, sentence, useRepos, useServes, useWorktrees, useWrite } from '@handline/app-data';
import { Button, Empty, Segmented, Select } from './ui/index.ts';
import { setting } from './daemon.ts';
import type { KV } from '@handline/app-core';
import { WorktreeBoard, type Moves } from './WorktreeBoard.tsx';
import { goAgent, goWorktrees } from './route.ts';
import { View } from './View.tsx';
import { WorktreePanel } from './WorktreePanel.tsx';

/** A snapshot older than this says when it was read, with a Refresh. */
const OLD_MS = 30_000;
const NO_OWNER = '\u0000none';

export const sums = (w: Worktree) => ({
  added: w.changed.reduce((n, f) => n + f.added, 0),
  deleted: w.changed.reduce((n, f) => n + f.deleted, 0),
});

export function Owner({ w }: { w: Worktree }) {
  return w.owner ? (
    <button className="link" onClick={() => goAgent(w.owner!)}>
      {w.owner}
    </button>
  ) : (
    <span className="muted">no owner</span>
  );
}

const STATUS_TONE: Record<string, string> = { 'in-progress': 'text-working', 'in-review': 'text-wait', blocked: 'text-bad' };
const TD = 'h-[var(--trow-h)] border-b border-line px-4 text-body';

export function Status({ word }: { word: string }) {
  const key = word.replace(/\W+/g, '-');
  return <span className={`wt-status s-${key} text-label text-ink-2 ${STATUS_TONE[key] ?? ''}`}>{word}</span>;
}

/** Re-renders every few seconds, so a note about the data's age keeps up. */
function useNow(ms = 5000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

/** The status writes of this page: each is asked once, shown as pending until the refetch has read the new status, and refused with the daemon's reason. */
function useMoves(refetch: () => Promise<unknown>): Moves {
  const set = useWrite('worktree.set_status');
  const { served } = useServes('worktree.set_status');
  const [moving, setMoving] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const without = (r: Record<string, string>, k: string) => Object.fromEntries(Object.entries(r).filter(([p]) => p !== k));
  const move = (path: string, status: string) => {
    if (moving[path]) return;
    setMoving((m) => ({ ...m, [path]: status }));
    setErrors((e) => without(e, path));
    set
      .mutateAsync({ path, status })
      .then(() => refetch())
      .catch((e) => setErrors((x) => ({ ...x, [path]: sentence(classify(e)) })))
      .finally(() => setMoving((m) => without(m, path)));
  };
  return { enabled: served, moving, errors, move };
}

export function Worktrees({ path, storage }: { path: string | null; storage: KV | null }) {
  const q = useWorktrees();
  const moves = useMoves(q.refetch);
  const view = setting(storage, 'worktreesView', 'list');
  const [board, setBoard] = useState(view.value === 'board');
  const repos = useRepos();
  const { served } = useServes('worktrees.list');
  const [repo, setRepo] = useState('');
  const [owner, setOwner] = useState('');
  const [search, setSearch] = useState('');
  const now = useNow();
  const all = q.data?.worktrees ?? [];
  const find = search.trim().toLowerCase();
  const shown = all
    .filter(
      (w) =>
        (!repo || w.repo === repo) &&
        (!owner || (owner === NO_OWNER ? w.owner === null : w.owner === owner)) &&
        (!find || w.branch.toLowerCase().includes(find) || w.path.toLowerCase().includes(find)),
    )
    .sort((a, b) => a.repo.localeCompare(b.repo) || a.branch.localeCompare(b.branch));
  const repoNames = [...new Set([...(repos.data?.repos.map((r) => r.name) ?? []), ...all.map((w) => w.repo)])].sort();
  const owners = [...new Set(all.flatMap((w) => (w.owner ? [w.owner] : [])))].sort();
  const clear = () => (setRepo(''), setOwner(''), setSearch(''));
  const old = q.dataUpdatedAt > 0 && now - q.dataUpdatedAt > OLD_MS;
  const open = path ? all.find((w) => w.path === path) : undefined;
  return (
    <section className="worktrees flex min-h-0 min-w-0 flex-1 flex-col overflow-auto px-5 pb-7" aria-label="Worktrees">
      <header className="wt-head flex flex-wrap items-center gap-4 py-5">
        <h2 className="m-0 mr-4 text-title font-semibold">Worktrees</h2>
        <Select
          label="Repo"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          options={[{ value: '', label: 'all repos' }, ...repoNames.map((r) => ({ value: r, label: r }))]}
        />
        <Select
          label="Owner"
          value={owner}
          onChange={(e) => setOwner(e.target.value)}
          options={[
            { value: '', label: 'all owners' },
            { value: NO_OWNER, label: 'no owner' },
            ...owners.map((o) => ({ value: o, label: o })),
          ]}
        />
        <Segmented
          label="View"
          value={board ? 'board' : 'list'}
          onChange={(v) => (setBoard(v === 'board'), view.save(v))}
          options={[
            { value: 'list', label: 'List' },
            { value: 'board', label: 'Board' },
          ]}
        />
        <input
          className="search"
          type="search"
          placeholder="Search branch or path"
          aria-label="Search worktrees"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {old ? (
          <span className="wt-old muted ml-auto inline-flex items-center gap-3" role="status">
            refreshed {ago(new Date(q.dataUpdatedAt).toISOString(), now)} ago{' '}
            <Button variant="outline" size="sm" icon="refresh" label="Refresh" onClick={() => void q.refetch()}>
              Refresh
            </Button>
          </span>
        ) : null}
      </header>
      <View q={q} served={served} shape="worktrees" what="worktrees">
        <div
          className={`wt-body grid grid-cols-[minmax(0,1fr)] items-start gap-6${path ? ' grid-cols-[minmax(0,1fr)_minmax(280px,380px)] max-[760px]:grid-cols-[minmax(0,1fr)]' : ''}`}
        >
          {!q.data ? null : all.length === 0 ? (
            <Empty icon="worktrees" name="No worktrees yet." hint="A worktree appears when an agent is started in a repo." />
          ) : shown.length === 0 ? (
            <div className="wt-none grid justify-items-center gap-5 py-7">
              <Empty icon="filter" name="No worktree matches" />
              <Button variant="outline" label="Clear filters" onClick={clear} />
            </div>
          ) : board ? (
            <WorktreeBoard rows={shown} path={path} moves={moves} />
          ) : (
            <table className="wt-table w-full border-collapse">
              <thead>
                <tr>
                  <th className="border-b border-line p-4 text-left text-label font-normal text-ink-2">Repo</th>
                  <th className="border-b border-line p-4 text-left text-label font-normal text-ink-2">Branch</th>
                  <th className="border-b border-line p-4 text-left text-label font-normal text-ink-2">Owner</th>
                  <th className="border-b border-line p-4 text-left text-label font-normal text-ink-2">Status</th>
                  <th className="border-b border-line p-4 text-left text-label font-normal text-ink-2">Files</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((w) => {
                  const { added, deleted } = sums(w);
                  return (
                    <tr key={w.path} data-worktree={w.path} className={`${w.path === path ? 'on bg-panel' : ''}`}>
                      <td className={TD}>{w.repo}</td>
                      <td className={TD}>
                        <button className="link" aria-current={w.path === path ? 'true' : undefined} onClick={() => goWorktrees(w.path)}>
                          {w.branch}
                        </button>
                      </td>
                      <td className={TD}>
                        <Owner w={w} />
                      </td>
                      <td className={TD}>
                        <Status word={w.status} />
                      </td>
                      <td className={TD}>
                        {w.changed.length}
                        {w.changed.length ? (
                          <span className="muted">
                            {' '}
                            +{added} −{deleted}
                          </span>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {path && q.data ? <WorktreePanel worktree={open} path={path} all={all} moves={moves} onClose={() => goWorktrees()} /> : null}
        </div>
      </View>
    </section>
  );
}
