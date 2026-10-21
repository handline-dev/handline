// The status writes of the Worktrees page, for the PC and the phone: each is asked once, shown as pending until the refetch has read
// the new status, and refused with the daemon's reason.
import { useState } from 'react';
import { classify, sentence } from '#src/errors.ts';
import { useServes } from '#src/provider.ts';
import { useWrite } from '#src/write.ts';

export const STATUSES = ['todo', 'in progress', 'in review', 'done'];

/** What a status write does on this page: the call, the statuses being asked for, and the reasons a daemon gave. */
export interface Moves {
  enabled: boolean;
  moving: Record<string, string>;
  errors: Record<string, string>;
  move: (path: string, status: string) => void;
}

export function useMoves(refetch: () => Promise<unknown>): Moves {
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
