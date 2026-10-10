// One worktree beside the list: where it is, who owns it, how it relates to others, and the files it changed.
import type { Worktree } from '@handline/client';
import { Button, Select } from './ui/index.ts';
import { STATUSES, type Moves } from './WorktreeBoard.tsx';
import { goAgent, goWorktrees } from './route.ts';
import { Owner, Status, sums } from './Worktrees.tsx';

export function WorktreePanel({
  worktree: w,
  path,
  all,
  moves,
  onClose,
}: {
  worktree: Worktree | undefined;
  path: string;
  all: Worktree[];
  moves: Moves;
  onClose: () => void;
}) {
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
          {moves.enabled ? (
            <Select
              label="Status"
              value={w.status}
              disabled={!!moves.moving[w.path]}
              onChange={(e) => moves.move(w.path, e.target.value)}
              options={(STATUSES.includes(w.status) ? STATUSES : [w.status, ...STATUSES]).map((s) => ({ value: s, label: s }))}
            />
          ) : (
            <Status word={w.status} />
          )}
          {moves.moving[w.path] ? (
            <span className="muted" role="status">
              {' '}
              moving to {moves.moving[w.path]}…
            </span>
          ) : null}
          {moves.errors[w.path] ? (
            <span className="wt-error" role="alert">
              {' '}
              {moves.errors[w.path]}
            </span>
          ) : null}
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
      {w.changed.length ? (
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
