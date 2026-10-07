// One row of the Tasks table or tree: the cells, the cursor, the mark and what a write in flight does to the state cell.
import { idleText, isOver, timeoutText } from '@handline/app-core';
import type { Task } from '@handline/client';
import { goTasks } from './route.ts';
import { AnimatedCell, AnimatedRow } from './AnimatedList.tsx';
import { StateChip } from './taskui.tsx';
import { Button } from './ui/index.ts';

export function Row(props: {
  t: Task;
  now: number;
  marked: boolean;
  cursor: boolean;
  open: boolean;
  mark: 'authored' | 'decided' | null;
  failed: boolean;
  tree?: boolean;
  depth?: number;
  rows: number;
  dim?: boolean;
  onCursor: () => void;
  onMark: () => void;
  can: { discard: { ok: boolean; why: string | undefined }; edit: { ok: boolean; why: string | undefined } };
  onAct: (what: 'discard' | 'owner' | 'priority') => void;
}) {
  const { t, now } = props;
  const over = isOver(t, now);
  const detail = [
    `asked by ${t.from}`,
    idleText(t, now) && `idle ${idleText(t, now)}`,
    timeoutText(t, now) && `time box ${timeoutText(t, now)}`,
  ]
    .filter(Boolean)
    .join(' · ');
  return (
    <AnimatedRow
      rows={props.rows}
      className={`trow${props.cursor ? ' cursor' : ''}${props.open ? ' open' : ''}${props.dim ? ' dim' : ''}${props.mark === 'authored' ? ' pending' : ''}`}
      aria-selected={props.cursor}
      data-task={t.id}
      onClick={() => {
        props.onCursor();
        if (t.id > 0) goTasks(t.id);
      }}
    >
      <AnimatedCell className="c-mark">
        <input
          type="checkbox"
          aria-label={`Mark #${t.id}`}
          checked={props.marked}
          onChange={props.onMark}
          onClick={(e) => e.stopPropagation()}
        />
      </AnimatedCell>
      {props.tree ? (
        <>
          <AnimatedCell className="c-title" style={{ paddingLeft: 8 + (props.depth ?? 0) * 18 }} title={detail}>
            <a href={`#/tasks/${t.id}`} className="tnum mono" onClick={(e) => e.stopPropagation()}>
              #{t.id < 0 ? 'new' : t.id}
            </a>{' '}
            <span className="ttitle">{t.title}</span>
            <span className="tsub muted">{[t.to, t.parent ? `under #${t.parent}` : ''].filter(Boolean).join(' · ')}</span>
          </AnimatedCell>
          <AnimatedCell className="c-state">
            <StateChip state={t.state} mark={props.mark} />
            {props.failed ? <span className="not-changed"> not changed</span> : null}
          </AnimatedCell>
        </>
      ) : (
        <>
          <AnimatedCell className="c-num mono">
            <a href={`#/tasks/${t.id}`} className="tnum" onClick={(e) => e.stopPropagation()}>
              #{t.id < 0 ? 'new' : t.id}
            </a>
          </AnimatedCell>
          <AnimatedCell className="c-pri mono" title="priority, 1 high to 3 low">
            P{t.priority ?? 2}
          </AnimatedCell>
          <AnimatedCell className="c-state">
            <StateChip state={t.state} mark={props.mark} />
            {props.failed ? <span className="not-changed"> not changed</span> : null}
          </AnimatedCell>
          <AnimatedCell className="c-title" title={detail}>
            <span className={`ttitle${props.mark === 'authored' ? ' ghost' : ''}`}>{t.title}</span>
            <span className="tsub muted">
              {t.to} · {t.from}
            </span>
          </AnimatedCell>
          <AnimatedCell className={`c-time mono${over ? ' over' : ''}`} title={detail}>
            {timeoutText(t, now) || idleText(t, now)}
          </AnimatedCell>
        </>
      )}
      <AnimatedCell className="c-act">
        {t.id > 0 ? (
          <span className="tacts" onClick={(e) => e.stopPropagation()}>
            <Button
              icon="owner"
              label="Owner"
              size="sm"
              title={props.can.edit.why}
              disabled={!props.can.edit.ok}
              onClick={() => props.onAct('owner')}
            />
            <Button
              icon="priority"
              label="Priority"
              size="sm"
              title={props.can.edit.why}
              disabled={!props.can.edit.ok}
              onClick={() => props.onAct('priority')}
            />
            <Button
              icon="dismiss"
              label="Discard"
              size="sm"
              title={props.can.discard.why}
              disabled={!props.can.discard.ok}
              onClick={() => props.onAct('discard')}
            />
          </span>
        ) : null}
      </AnimatedCell>
    </AnimatedRow>
  );
}
