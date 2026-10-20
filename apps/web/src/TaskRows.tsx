// One row of the Tasks list: mark (on hover), state icon, number, title, owner and priority chips, time. A click opens the task; only the box marks.
import { idleText, isOver, isPerson, timeoutText, taskRef, watchResult, type RowVerb } from '@handline/app-core';
import type { Task } from '@handline/client';
import { useStore } from '@handline/app-react';
import { goTasks } from './route.ts';
import { AnimatedCell, AnimatedRow } from './AnimatedList.tsx';
import { StateChip } from './taskui.tsx';
import { RowMenu, type Cans } from './TaskVerbUi.tsx';

const CHIP = 'inline-block max-w-full truncate rounded-pill border border-line px-2 text-hint leading-5 align-middle';

/** "3h" for a task not running reads as the time it has: "3h left". */
const left = (text: string) => (/^[\dhdm]+$/.test(text) ? `${text} left` : text);

export function Row(props: {
  t: Task;
  now: number;
  marked: boolean;
  cursor: boolean;
  open: boolean;
  mark: 'authored' | 'decided' | null;
  failed: boolean;
  rows: number;
  /** Boxes show always once a row is marked, else on hover. */
  markCell: boolean;
  onCursor: () => void;
  onMark: (range: boolean) => void;
  /** The row's menu of verbs: ⋯ on hover, and a right-click. */
  verbs: { cans: Cans; onPick: (verb: RowVerb) => void };
}) {
  const { t, now } = props;
  const me = useStore().me;
  const over = isOver(t, now);
  const authored = props.mark === 'authored';
  const time = left(timeoutText(t, now) || idleText(t, now));
  // An open row keeps its raised ground when hovered; a cursor row carries the inset bar. On a phone the row is a card.
  const row = `trow group/trow cursor-default max-sm:mt-0 max-sm:mb-2 max-sm:grid max-sm:grid-cols-[auto_1fr_auto] max-sm:gap-x-2 max-sm:py-2 max-sm:px-3 max-sm:bg-panel max-sm:border max-sm:border-line max-sm:rounded-card max-sm:[grid-template-areas:'state_num_time'_'title_title_title'_'owner_pri_pri'] ${
    props.open ? 'open bg-raised hover:bg-raised' : 'hover:bg-panel'
  }${props.cursor ? ` cursor shadow-[inset_2px_0_0_var(--ink)]${props.open ? '' : ' bg-panel'}` : ''}${authored ? ' pending' : ''}`;
  const TD = 'max-sm:block max-sm:h-auto max-sm:p-0 max-sm:border-0 max-sm:w-auto';
  return (
    <RowMenu tasks={[t]} me={me} cans={props.verbs.cans} onPick={props.verbs.onPick}>
      {(more, menuOpen) => (
        <AnimatedRow
          rows={props.rows}
          className={row}
          aria-selected={props.cursor}
          data-task={t.id}
          onClick={() => {
            props.onCursor();
            if (t.id > 0) goTasks(t.id);
          }}
        >
          <AnimatedCell className={`c-mark w-8 text-center ${TD} max-sm:hidden`}>
            <input
              type="checkbox"
              className={props.markCell ? undefined : 'opacity-0 group-hover/trow:opacity-100 focus-visible:opacity-100'}
              aria-label={`Mark ${taskRef(t.id)}`}
              checked={props.marked}
              onChange={() => undefined}
              onClick={(e) => {
                e.stopPropagation();
                props.onMark(e.shiftKey);
              }}
            />
          </AnimatedCell>
          <AnimatedCell className={`c-state w-8 ${TD} max-sm:[grid-area:state]`}>
            <StateChip state={t.state} mark={props.mark} to={t.to} result={watchResult(t)} />
            {props.failed ? <span className="not-changed text-bad text-hint"> not changed</span> : null}
          </AnimatedCell>
          <AnimatedCell className={`c-num mono w-16 ${TD} max-sm:[grid-area:num]`}>
            <a href={`#/tasks/${t.id}`} className="tnum text-inherit no-underline hover:underline" onClick={(e) => e.stopPropagation()}>
              {t.id < 0 ? 'new' : taskRef(t.id)}
            </a>
          </AnimatedCell>
          <AnimatedCell
            className={`c-title min-w-40 ${TD} max-sm:[grid-area:title] max-sm:whitespace-normal max-sm:[display:-webkit-box] max-sm:[-webkit-line-clamp:2] max-sm:[-webkit-box-orient:vertical]`}
            title={`asked by ${t.from}`}
          >
            <span className={`ttitle text-body font-medium${authored ? ' ghost italic text-ink-2' : ''}`}>{t.title}</span>
          </AnimatedCell>
          <AnimatedCell className={`c-owner w-44 text-right ${TD} max-sm:[grid-area:owner] max-sm:text-left`}>
            <span className={CHIP} title="owner">
              {isPerson(t.to, me) ? 'you' : t.to}
            </span>
          </AnimatedCell>
          <AnimatedCell className={`c-pri mono w-14 ${TD} max-sm:[grid-area:pri] max-sm:text-right`} title="priority, 1 high to 3 low">
            <span className={CHIP}>P{t.priority ?? 2}</span>
          </AnimatedCell>
          <AnimatedCell
            className={`c-time mono w-28 whitespace-nowrap text-right ${TD} max-sm:[grid-area:time] max-sm:text-ink-2${over ? ' text-bad font-semibold' : ''}`}
          >
            <span className="inline-flex items-center gap-1">
              <span
                className={`inline-flex ${menuOpen ? 'opacity-100' : 'opacity-0 group-hover/trow:opacity-100 focus-within:opacity-100 pointer-coarse:opacity-100'}`}
              >
                {more}
              </span>
              {time}
            </span>
          </AnimatedCell>
        </AnimatedRow>
      )}
    </RowMenu>
  );
}
