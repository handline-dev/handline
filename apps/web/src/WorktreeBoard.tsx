// The Worktrees board: one column per status of docs/design/new-agent-worktrees.md §2.2, and an "other" column for any status outside them.
// A drop, the Move to… menu and the key `m` all ask the daemon for the same status; a card moves only when the refetch shows it.
import { useState } from 'react';
import type { Worktree } from '@handline/client';
import { Button } from './ui/index.ts';
import { goWorktrees } from './route.ts';
import { Owner, sums } from './Worktrees.tsx';

export const STATUSES = ['todo', 'in progress', 'in review', 'done'];

/** What a status write does on this page: the call, the statuses being asked for, and the reasons a daemon gave. */
export interface Moves {
  enabled: boolean;
  moving: Record<string, string>;
  errors: Record<string, string>;
  move: (path: string, status: string) => void;
}

export function WorktreeBoard({ rows, path, moves }: { rows: Worktree[]; path: string | null; moves: Moves }) {
  const [dragging, setDragging] = useState<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const known = (w: Worktree) => STATUSES.includes(w.status);
  const columns = [...STATUSES, ...(rows.some((w) => !known(w)) ? ['other'] : [])];
  const drop = (status: string) => {
    if (dragging && status !== 'other') moves.move(dragging, status);
    setDragging(null);
  };
  return (
    <div className="wt-board" role="group" aria-label="Worktree board">
      {columns.map((col) => {
        const cards = rows.filter((w) => (col === 'other' ? !known(w) : w.status === col));
        return (
          <section
            key={col}
            className="wt-col"
            data-status={col}
            aria-label={`${col}, ${cards.length}`}
            onDragOver={(e) => col !== 'other' && moves.enabled && e.preventDefault()}
            onDrop={() => drop(col)}
          >
            <h3>
              {col} <span className="muted">{cards.length}</span>
            </h3>
            {cards.map((w) => {
              const { added, deleted } = sums(w);
              return (
                <article
                  key={w.path}
                  className={`wt-card${w.path === path ? ' on' : ''}`}
                  data-worktree={w.path}
                  tabIndex={0}
                  draggable={moves.enabled && !moves.moving[w.path]}
                  onDragStart={() => setDragging(w.path)}
                  onDragEnd={() => setDragging(null)}
                  onKeyDown={(e) => {
                    if (e.key === 'm' && moves.enabled && e.target === e.currentTarget) setMenu(menu === w.path ? null : w.path);
                    if (e.key === 'Escape') setMenu(null);
                  }}
                >
                  <button className="link" onClick={() => goWorktrees(w.path)}>
                    {w.branch}
                  </button>
                  <span className="muted">
                    {w.repo} · <Owner w={w} />
                  </span>
                  <span className="muted">
                    {w.changed.length} files{w.changed.length ? ` +${added} −${deleted}` : ''}
                  </span>
                  {moves.moving[w.path] ? (
                    <span className="wt-moving" role="status">
                      moving to {moves.moving[w.path]}…
                    </span>
                  ) : null}
                  {moves.errors[w.path] ? (
                    <span className="wt-error" role="alert">
                      {moves.errors[w.path]}
                    </span>
                  ) : null}
                  {moves.enabled ? (
                    <Button
                      size="sm"
                      variant="outline"
                      label="Move to…"
                      disabled={!!moves.moving[w.path]}
                      aria-haspopup="menu"
                      aria-expanded={menu === w.path}
                      onClick={() => setMenu(menu === w.path ? null : w.path)}
                    >
                      Move to…
                    </Button>
                  ) : null}
                  {menu === w.path ? (
                    <div className="wt-menu" role="menu" aria-label="Move to">
                      {STATUSES.filter((s) => s !== w.status).map((s) => (
                        <button
                          key={s}
                          role="menuitem"
                          className="ui-prow rounded-chip max-sm:min-h-12"
                          onClick={() => (setMenu(null), moves.move(w.path, s))}
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </article>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}
