// One row of the Tasks table or tree: the cells, the cursor, the mark and what a write in flight does to the state cell.
import { idleText, isOver, timeoutText } from '@handline/app-core';
import type { Task } from '@handline/client';
import { goTasks } from './route.ts';
import { StateChip } from './taskui.tsx';

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
  dim?: boolean;
  onCursor: () => void;
  onMark: () => void;
}) {
  const { t, now } = props;
  const over = isOver(t, now);
  return (
    <tr
      className={`trow${props.cursor ? ' cursor' : ''}${props.open ? ' open' : ''}${props.dim ? ' dim' : ''}${props.mark === 'authored' ? ' pending' : ''}`}
      aria-selected={props.cursor}
      data-task={t.id}
      onClick={props.onCursor}
      onDoubleClick={() => goTasks(t.id)}
    >
      <td className="c-mark">
        <input
          type="checkbox"
          aria-label={`Mark #${t.id}`}
          checked={props.marked}
          onChange={props.onMark}
          onClick={(e) => e.stopPropagation()}
        />
      </td>
      {props.tree ? (
        <>
          <td className="c-title" style={{ paddingLeft: 8 + (props.depth ?? 0) * 18 }}>
            <a href={`#/tasks/${t.id}`} className="tnum mono">
              #{t.id < 0 ? 'new' : t.id}
            </a>{' '}
            <span className="ttitle">{t.title}</span>
          </td>
          <td className="c-owner">{t.to}</td>
          <td className="c-state">
            <StateChip state={t.state} mark={props.mark} />
            {props.failed ? <span className="not-changed"> not changed</span> : null}
          </td>
          <td className="c-under mono">{t.parent ? `#${t.parent}` : ''}</td>
        </>
      ) : (
        <>
          <td className="c-num mono">
            <a href={`#/tasks/${t.id}`} className="tnum" onClick={(e) => e.stopPropagation()}>
              #{t.id < 0 ? 'new' : t.id}
            </a>
          </td>
          <td className="c-pri mono" title="priority, 1 high to 3 low">
            P{t.priority ?? 2}
          </td>
          <td className="c-state">
            <StateChip state={t.state} mark={props.mark} />
            {props.failed ? <span className="not-changed"> not changed</span> : null}
          </td>
          <td className="c-title">
            <span className={`ttitle${props.mark === 'authored' ? ' ghost' : ''}`}>{t.title}</span>
          </td>
          <td className="c-owner">{t.to}</td>
          <td className="c-idle mono">{idleText(t, now)}</td>
          <td className={`c-time mono${over ? ' over' : ''}`}>{timeoutText(t, now)}</td>
          <td className="c-asker">{t.from}</td>
        </>
      )}
    </tr>
  );
}
