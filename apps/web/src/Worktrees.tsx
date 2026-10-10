// The Worktrees page: every worktree the daemon knows, with repo and owner filters (client-side) and a search, and a side panel for one.
// Read only: a worktree is made by starting an agent, and its status is a label only the daemon sets.
import { useEffect, useState } from 'react';
import { ago } from '@handline/app-core';
import type { Worktree } from '@handline/client';
import { useRepos, useServes, useWorktrees } from '@handline/app-data';
import { Button, Empty, Select } from './ui/index.ts';
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

export function Status({ word }: { word: string }) {
  return <span className={`wt-status s-${word.replace(/\W+/g, '-')}`}>{word}</span>;
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

export function Worktrees({ path }: { path: string | null }) {
  const q = useWorktrees();
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
    <section className="worktrees" aria-label="Worktrees">
      <header className="wt-head">
        <h2>Worktrees</h2>
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
        <input
          className="search"
          type="search"
          placeholder="Search branch or path"
          aria-label="Search worktrees"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {old ? (
          <span className="wt-old muted" role="status">
            refreshed {ago(new Date(q.dataUpdatedAt).toISOString(), now)} ago{' '}
            <Button variant="outline" size="sm" icon="refresh" label="Refresh" onClick={() => void q.refetch()}>
              Refresh
            </Button>
          </span>
        ) : null}
      </header>
      <View q={q} served={served} shape="worktrees" what="worktrees">
        <div className={`wt-body${path ? ' with-panel' : ''}`}>
          {all.length === 0 ? (
            <Empty icon="worktrees" name="No worktrees yet." hint="A worktree appears when an agent is started in a repo." />
          ) : shown.length === 0 ? (
            <div className="wt-none">
              <Empty icon="filter" name="No worktree matches" />
              <Button variant="outline" label="Clear filters" onClick={clear} />
            </div>
          ) : (
            <table className="wt-table">
              <thead>
                <tr>
                  <th>Repo</th>
                  <th>Branch</th>
                  <th>Owner</th>
                  <th>Status</th>
                  <th>Files</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((w) => {
                  const { added, deleted } = sums(w);
                  return (
                    <tr key={w.path} data-worktree={w.path} className={w.path === path ? 'on' : undefined}>
                      <td>{w.repo}</td>
                      <td>
                        <button className="link" aria-current={w.path === path ? 'true' : undefined} onClick={() => goWorktrees(w.path)}>
                          {w.branch}
                        </button>
                      </td>
                      <td>
                        <Owner w={w} />
                      </td>
                      <td>
                        <Status word={w.status} />
                      </td>
                      <td>
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
          {path ? <WorktreePanel worktree={open} path={path} all={all} onClose={() => goWorktrees()} /> : null}
        </div>
      </View>
    </section>
  );
}
