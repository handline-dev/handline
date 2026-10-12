// The header of the Tasks page: search, the Open / Mine / Closed pills, the Filters popover (agent and states), list or tree, select (phone), keys and New task.
import type { ReactNode } from 'react';
import { CLOSED, LISTED, TASK_STATES, type TaskColumn, type TaskFilter } from '@handline/app-core';
import { BarInput, Button, Icon, Popup, Segmented, Select, Toolbar } from './ui/index.ts';
import { STATE_GLYPH } from './taskui.tsx';

/** Which tasks the pills show: the open ones, the ones I own, or the closed ones. */
export type Scope = 'open' | 'mine' | 'closed';

export const HEADS: { col: TaskColumn; label: string; cls: string }[] = [
  { col: 'number', label: '#', cls: 'c-num w-16' },
  { col: 'priority', label: 'P', cls: 'c-pri w-10' },
  { col: 'state', label: 'state', cls: 'c-state w-16' },
  { col: 'title', label: 'title', cls: 'c-title' },
  { col: 'timeout', label: 'time', cls: 'c-time w-20' },
];
/** What the `s` key steps through: the columns shown and the ones folded into the title line. */
export const SORTS: TaskColumn[] = ['number', 'priority', 'state', 'title', 'owner', 'idle', 'timeout', 'asker'];

type Mode = 'list' | 'tree';

/** True when the filters differ from what a list opens with. */
const changed = (f: TaskFilter): boolean => !!f.agent || f.states.size !== LISTED.length || LISTED.some((s) => !f.states.has(s));

export function TasksHead(props: {
  agents: string[];
  filter: TaskFilter;
  mode: Mode;
  selecting: boolean;
  scope: Scope;
  onScope: (s: Scope) => void;
  filtering: boolean;
  query: string;
  onQuery: (q: string) => void;
  onFilter: (f: TaskFilter) => void;
  onView: (m: Mode) => void;
  onSelecting: () => void;
  onAct: (what: 'states' | 'keys' | 'new') => void;
}) {
  const { filter, mode, selecting } = props;
  return (
    <header className="tasks-head flex flex-wrap items-center gap-2 pb-2">
      <h1 className="m-0 mr-auto text-page font-semibold">Tasks</h1>
      <label className="tsearch relative inline-flex items-center" title="Search by number, title or agent">
        <Icon name="search" label="Search" size={14} aria-hidden="true" className="pointer-events-none absolute left-1.5 text-ink-3" />
        <BarInput
          type="search"
          value={props.query}
          placeholder="Search"
          aria-label="Search tasks"
          className="w-[200px] pl-6 max-sm:w-full"
          onChange={(e) => props.onQuery(e.target.value)}
        />
      </label>
      <Segmented
        label="Show"
        value={props.scope}
        onChange={props.onScope}
        options={[
          { value: 'open', label: 'Open' },
          { value: 'mine', label: 'Mine' },
          { value: 'closed', label: 'Closed' },
        ]}
      />
      <span className="tfilters relative inline-flex">
        <Button
          icon="filter"
          label={changed(filter) ? 'Filters, on' : 'Filters'}
          aria-label={changed(filter) ? 'Filters, on' : 'Filters'}
          title="Filters (v)"
          variant={props.filtering ? 'outline' : 'ghost'}
          className={`relative w-7 p-0 max-sm:min-h-[44px]${props.filtering ? ' [background:var(--bg-3)]' : ''}`}
          aria-expanded={props.filtering}
          onClick={() => props.onAct('states')}
        >
          {changed(filter) ? (
            <span className="tdot absolute top-1 right-1 h-2 w-2 rounded-pill border border-canvas bg-ink" title="A filter is on" />
          ) : null}
        </Button>
        <Popup open={props.filtering} onClose={() => props.onAct('states')} title="Filters">
          <div className="tfilter-body grid gap-2 px-3 py-2">
            <Select
              label="Agent"
              className="max-sm:min-h-[44px]"
              value={filter.agent}
              onChange={(e) => props.onFilter({ ...filter, agent: e.target.value })}
              options={[{ value: '', label: 'all agents' }, ...props.agents.map((a) => ({ value: a, label: a }))]}
            />
            {TASK_STATES.filter((s) => !CLOSED.includes(s)).map((s) => (
              <label key={s} className="tcheck inline-flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={filter.states.has(s)}
                  onChange={(e) => {
                    const states = new Set(filter.states);
                    if (e.target.checked) states.add(s);
                    else states.delete(s);
                    props.onFilter({ ...filter, states });
                  }}
                />
                <Icon name={STATE_GLYPH[s] ?? 'taskPending'} size={14} aria-hidden="true" /> {s}
              </label>
            ))}
            <Button
              variant="outline"
              size="sm"
              label="Reset"
              onClick={() => props.onFilter({ ...filter, states: new Set(LISTED), agent: '' })}
            />
          </div>
        </Popup>
      </span>
      <Segmented
        label="View"
        value={mode}
        onChange={props.onView}
        options={[
          { value: 'list', label: 'list', icon: 'viewList' },
          { value: 'tree', label: 'tree', icon: 'viewTree' },
        ]}
      />
      <Button
        icon="select"
        label="Select"
        title="Select rows"
        aria-pressed={selecting}
        variant={selecting ? 'outline' : 'ghost'}
        className={`max-sm:min-h-[44px]${selecting ? ' [background:var(--bg-3)]' : ''}`}
        onClick={() => props.onSelecting()}
      />
      <Button icon="keys" label="Keys" title="Keys (?)" className="max-sm:min-h-[44px]" onClick={() => props.onAct('keys')} />
      <Button
        icon="plus"
        label="New task"
        title="New task (n)"
        variant="solid"
        className="max-sm:min-h-[44px]"
        onClick={() => props.onAct('new')}
      />
    </header>
  );
}

type Can = { ok: boolean; why: string | undefined };

/** The icon bar under the table while rows are marked: it acts on them. One row's own actions are on the row. */
export function TasksBar(props: {
  marked: number;
  /** How many rows are shown: the bar offers to mark them all. */
  shown?: number;
  can: { discard: Can; edit: Can; merge: Can };
  onAct: (what: 'discard' | 'owner' | 'priority' | 'merge') => void;
  onAll?: () => void;
  children?: ReactNode;
}) {
  const { can } = props;
  return (
    <Toolbar label="Actions on the marked tasks">
      <span className="muted mr-auto">{props.marked} marked</span>
      {props.onAll && props.shown !== undefined ? (
        <Button variant="outline" size="sm" label={props.marked >= props.shown ? 'Clear' : 'Select all'} onClick={props.onAll}>
          {props.marked >= props.shown ? 'Clear' : `Select all ${props.shown}`}
        </Button>
      ) : null}
      <Button
        icon="dismiss"
        label="Discard"
        title={can.discard.why ?? 'Discard (d)'}
        disabled={!can.discard.ok}
        className="max-sm:min-h-[44px]"
        onClick={() => props.onAct('discard')}
      />
      <Button
        icon="owner"
        label="Owner"
        title={can.edit.why ?? 'Owner (o)'}
        disabled={!can.edit.ok}
        className="max-sm:min-h-[44px]"
        onClick={() => props.onAct('owner')}
      />
      <Button
        icon="priority"
        label="Priority"
        title={can.edit.why ?? 'Priority (p)'}
        disabled={!can.edit.ok}
        className="max-sm:min-h-[44px]"
        onClick={() => props.onAct('priority')}
      />
      <Button
        icon="merge"
        label="Merge"
        title={can.merge.why ?? 'Merge (m): mark tasks, put the cursor on the one to keep'}
        disabled={!can.merge.ok}
        className="max-sm:min-h-[44px]"
        onClick={() => props.onAct('merge')}
      />
      {props.children}
    </Toolbar>
  );
}
