import type { TaskColumn } from '@handline/app-core';
import { Icon } from './ui/Icon.tsx';
import { HEADS } from './TasksHead.tsx';

/** Every head cell: sticky over the scroll, the label type on the surface. */
const TH = 'sticky top-0 border-b border-line p-0 text-left font-normal text-label text-ink-2 bg-surface';

export function TableHead(props: {
  tree: boolean;
  sort: { by: TaskColumn; reverse: boolean };
  allMarked: boolean;
  /** The mark column shows only while the table selects (or rows are marked); hidden otherwise so the columns line up. */
  markCell: boolean;
  onToggleAll: () => void;
  onSort: (col: TaskColumn) => void;
}) {
  const { sort } = props;
  return (
    <thead className="max-sm:hidden">
      <tr>
        <th className={`${TH} c-mark w-8 text-center${props.markCell ? '' : ' hidden'}`}>
          <input type="checkbox" aria-label="Mark every row shown" checked={props.allMarked} onChange={props.onToggleAll} />
        </th>
        {props.tree ? (
          <>
            <th className={`${TH} c-title`}>task</th>
            <th className={`${TH} c-state w-16`}>state</th>
          </>
        ) : (
          HEADS.map((h) => (
            <th
              key={h.col}
              className={`${TH} ${h.cls}`}
              aria-sort={sort.by === h.col ? (sort.reverse ? 'descending' : 'ascending') : undefined}
            >
              <button className="head border-0 bg-transparent p-2" onClick={() => props.onSort(h.col)}>
                {h.label}
                {sort.by === h.col ? (
                  <Icon name={sort.reverse ? 'sortDesc' : 'sortAsc'} size={11} aria-hidden className="ml-0.5 inline-block align-[-1px]" />
                ) : null}
              </button>
            </th>
          ))
        )}
        <th className={`${TH} c-act w-[1%] whitespace-nowrap`} aria-label="Actions" />
      </tr>
    </thead>
  );
}
