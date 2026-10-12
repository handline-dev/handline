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
      <aside className="wt-panel grid min-w-0 gap-3 rounded-card border border-line bg-panel p-3" aria-label="Worktree">
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
    <aside className="wt-panel grid min-w-0 gap-3 rounded-card border border-line bg-panel p-3" aria-label="Worktree">
      <header className="flex items-center justify-between gap-2">
        <strong>{w.branch}</strong>
        <Button icon="close" label="Close the panel" onClick={onClose} />
      </header>
      <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1.5">
        <dt className="text-ink-3">Repo</dt>
        <dd className="m-0 min-w-0 [overflow-wrap:anywhere]">{w.repo}</dd>
        <dt className="text-ink-3">Path</dt>
        <dd className="mono m-0 min-w-0 [overflow-wrap:anywhere]">{w.path}</dd>
        <dt className="text-ink-3">Owner</dt>
        <dd className="m-0 min-w-0 [overflow-wrap:anywhere]">
          <Owner w={w} />
        </dd>
        <dt className="text-ink-3">Status</dt>
        <dd className="m-0 min-w-0 [overflow-wrap:anywhere]">
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
            <span className="wt-error text-bad" role="alert">
              {' '}
              {moves.errors[w.path]}
            </span>
          ) : null}
        </dd>
        {w.parent ? (
          <>
            <dt className="text-ink-3">Parent</dt>
            <dd className="m-0 min-w-0 [overflow-wrap:anywhere]">{link(w.parent)}</dd>
          </>
        ) : null}
        {w.children.length ? (
          <>
            <dt className="text-ink-3">Children</dt>
            <dd className="m-0 min-w-0 [overflow-wrap:anywhere]">{w.children.map(link)}</dd>
          </>
        ) : null}
      </dl>
      {w.owner ? <Button variant="solid" label="Open agent" onClick={() => goAgent(w.owner!)} /> : null}
      {w.changed.length ? (
        <table className="wt-files w-full border-collapse text-body" aria-label="Changed files">
          <caption className="muted pb-1.5 text-left">
            {w.changed.length} changed +{added} -{deleted}
          </caption>
          <tbody>
            {w.changed.map((f) => (
              <tr key={f.path}>
                <td className="wt-letter w-[1.5em] border-t border-line px-1.5 py-1 font-semibold [overflow-wrap:anywhere]">{f.status}</td>
                <td className="mono border-t border-line px-1.5 py-1 [overflow-wrap:anywhere]">{f.path}</td>
                <td className="num border-t border-line px-1.5 py-1 text-right whitespace-nowrap [overflow-wrap:anywhere]">+{f.added}</td>
                <td className="num border-t border-line px-1.5 py-1 text-right whitespace-nowrap [overflow-wrap:anywhere]">-{f.deleted}</td>
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
