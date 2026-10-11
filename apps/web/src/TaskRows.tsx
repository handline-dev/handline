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
  /** The mark column shows while the table selects (or rows are marked); the phone card keeps a slot for it then. */
  markCell: boolean;
  dim?: boolean;
  onCursor: () => void;
  onMark: () => void;
  can: { discard: { ok: boolean; why: string | undefined }; edit: { ok: boolean; why: string | undefined } };
  onAct: (what: 'discard' | 'owner' | 'priority') => void;
}) {
  const { t, now } = props;
  const over = isOver(t, now);
  const authored = props.mark === 'authored';
  const detail = [
    `asked by ${t.from}`,
    idleText(t, now) && `idle ${idleText(t, now)}`,
    timeoutText(t, now) && `time box ${timeoutText(t, now)}`,
  ]
    .filter(Boolean)
    .join(' · ');
  // An open row keeps its raised ground when hovered; a cursor row carries the inset bar. On a phone the row is a card,
  // with #number, priority and state on line one (the mark column only while the table selects), then the title, then the time.
  const CARD = 'max-sm:mt-0 max-sm:grid max-sm:gap-y-1 max-sm:gap-x-4 max-sm:mb-4 max-sm:py-4 max-sm:px-5 max-sm:bg-panel max-sm:border max-sm:border-line max-sm:rounded-card';
  const card = props.markCell
    ? `${CARD} max-sm:grid-cols-[auto_1fr_auto_auto] max-sm:[grid-template-areas:'mark_num_pri_state'_'title_title_title_title'_'time_time_time_act']`
    : `${CARD} max-sm:grid-cols-[1fr_auto_auto] max-sm:[grid-template-areas:'num_pri_state'_'title_title_title'_'time_time_act']`;
  const row = `trow group/trow cursor-default ${card} ${props.open ? 'open bg-raised hover:bg-raised' : 'hover:bg-panel'}${
    props.cursor ? ` cursor shadow-[inset_2px_0_0_var(--ink)]${props.open ? '' : ' bg-panel'}` : ''
  }${props.dim ? ' dim' : ''}${authored ? ' pending' : ''}`;
  const TD = 'max-sm:block max-sm:h-auto max-sm:p-0 max-sm:border-0 max-sm:w-auto';
  return (
    <AnimatedRow rows={props.rows} className={row} aria-selected={props.cursor} data-task={t.id} onClick={() => {
      props.onCursor();
      if (t.id > 0) goTasks(t.id);
    }}>
      <AnimatedCell className={`c-mark w-8 text-center ${TD} max-sm:[grid-area:mark]${props.markCell ? '' : ' hidden'}`}>
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
          <AnimatedCell className={`c-title ${TD} max-sm:[grid-area:title] max-sm:whitespace-normal max-sm:[display:-webkit-box] max-sm:[-webkit-line-clamp:2] max-sm:[-webkit-box-orient:vertical]`} style={{ paddingLeft: 8 + (props.depth ?? 0) * 18 }} title={detail}>
            <a href={`#/tasks/${t.id}`} className="tnum mono text-inherit no-underline hover:underline" onClick={(e) => e.stopPropagation()}>
              #{t.id < 0 ? 'new' : t.id}
            </a>{' '}
            <span className={`ttitle text-body font-medium${authored ? ' ghost italic text-ink-2' : ''}`}>{t.title}</span>
            <span className="tsub block overflow-hidden text-ellipsis text-hint muted">
              {[t.to, t.parent ? `under #${t.parent}` : ''].filter(Boolean).join(' · ')}
            </span>
          </AnimatedCell>
          <AnimatedCell className={`c-state w-16 ${TD} max-sm:[grid-area:state]`}>
            <StateChip state={t.state} mark={props.mark} />
            {props.failed ? <span className="not-changed text-bad text-hint"> not changed</span> : null}
          </AnimatedCell>
        </>
      ) : (
        <>
          <AnimatedCell className={`c-num mono w-16 ${TD} max-sm:[grid-area:num]`}>
            <a href={`#/tasks/${t.id}`} className="tnum text-inherit no-underline hover:underline" onClick={(e) => e.stopPropagation()}>
              #{t.id < 0 ? 'new' : t.id}
            </a>
          </AnimatedCell>
          <AnimatedCell className={`c-pri mono w-10 ${TD} max-sm:[grid-area:pri]`} title="priority, 1 high to 3 low">
            P{t.priority ?? 2}
          </AnimatedCell>
          <AnimatedCell className={`c-state w-16 ${TD} max-sm:[grid-area:state]`}>
            <StateChip state={t.state} mark={props.mark} />
            {props.failed ? <span className="not-changed text-bad text-hint"> not changed</span> : null}
          </AnimatedCell>
          <AnimatedCell className={`c-title ${TD} max-sm:[grid-area:title] max-sm:whitespace-normal max-sm:[display:-webkit-box] max-sm:[-webkit-line-clamp:2] max-sm:[-webkit-box-orient:vertical]`} title={detail}>
            <span className={`ttitle text-body font-medium${authored ? ' ghost italic text-ink-2' : ''}`}>{t.title}</span>
            <span className="tsub block overflow-hidden text-ellipsis text-hint muted">
              {t.to} · {t.from}
            </span>
          </AnimatedCell>
          <AnimatedCell className={`c-time mono w-20 ${TD} max-sm:[grid-area:time] max-sm:text-ink-2${over ? ' text-bad font-semibold' : ''}`} title={detail}>
            {timeoutText(t, now) || idleText(t, now)}
          </AnimatedCell>
        </>
      )}
      <AnimatedCell className={`c-act w-[1%] whitespace-nowrap ${TD} max-sm:[grid-area:act]`}>
        {t.id > 0 ? (
          <span
            className={`tacts inline-flex gap-0.5 ${
              props.cursor ? 'opacity-100' : 'opacity-0 group-hover/trow:opacity-100 group-focus-within/trow:opacity-100 pointer-coarse:opacity-100'
            }`}
            onClick={(e) => e.stopPropagation()}
          >
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
