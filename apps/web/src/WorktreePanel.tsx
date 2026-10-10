// One worktree beside the list: where it is, who owns it, how it relates to others, and the files it changed.
import { useState } from 'react';
import type { Worktree } from '@handline/client';
import { useWorktreeDiff } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { Button } from './ui/index.ts';
import { goAgent, goWorktrees } from './route.ts';
import { Owner, Status, sums } from './Worktrees.tsx';

export function WorktreePanel({
  worktree: w,
  path,
  all,
  onClose,
}: {
  worktree: Worktree | undefined;
  path: string;
  all: Worktree[];
  onClose: () => void;
}) {
  const review = !!useStore().client.hello?.capabilities.review;
  const [tab, setTab] = useState<'files' | 'changes'>('files');
  if (!w)
    return (
      <aside className="wt-panel" aria-label="Worktree">
        <p className="muted">No worktree at {path}.</p>
        <Button variant="outline" label="Close" onClick={onClose} />
      </aside>
    );
  const { added, deleted } = sums(w);
  const link = (p: string) => {
    const other = all.find((x) => x.path === p);
    return (
      <button key={p} className="link" onClick={() => goWorktrees(p)}>
        {other?.branch ?? p}
      </button>
    );
  };
  return (
    <aside className="wt-panel" aria-label="Worktree">
      <header>
        <strong>{w.branch}</strong>
        <Button icon="close" label="Close the panel" onClick={onClose} />
      </header>
      <dl>
        <dt>Repo</dt>
        <dd>{w.repo}</dd>
        <dt>Path</dt>
        <dd className="mono">{w.path}</dd>
        <dt>Owner</dt>
        <dd>
          <Owner w={w} />
        </dd>
        <dt>Status</dt>
        <dd>
          <Status word={w.status} />
        </dd>
        {w.parent ? (
          <>
            <dt>Parent</dt>
            <dd>{link(w.parent)}</dd>
          </>
        ) : null}
        {w.children.length ? (
          <>
            <dt>Children</dt>
            <dd>{w.children.map(link)}</dd>
          </>
        ) : null}
      </dl>
      {w.owner ? <Button variant="solid" label="Open agent" onClick={() => goAgent(w.owner!)} /> : null}
      {review ? (
        <div role="tablist" aria-label="Worktree views" className="wt-tabs">
          {(['files', 'changes'] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} className={`chat-tab${tab === t ? ' on' : ''}`} onClick={() => setTab(t)}>
              {t === 'files' ? 'Files' : 'Changes'}
            </button>
          ))}
        </div>
      ) : null}
      {review && tab === 'changes' ? (
        <Changes path={w.path} />
      ) : w.changed.length ? (
        <table className="wt-files" aria-label="Changed files">
          <caption className="muted">
            {w.changed.length} changed +{added} −{deleted}
          </caption>
          <tbody>
            {w.changed.map((f) => (
              <tr key={f.path}>
                <td className="wt-letter">{f.status}</td>
                <td className="mono">{f.path}</td>
                <td className="num">+{f.added}</td>
                <td className="num">−{f.deleted}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">No changed files.</p>
      )}
    </aside>
  );
}

function Changes({ path }: { path: string }) {
  const q = useWorktreeDiff(path);
  if (q.isError) return <p className="muted">The diff could not be read.</p>;
  return <pre className="wt-diff">{q.data ? q.data.diff || 'No changes.' : 'Loading…'}</pre>;
}
