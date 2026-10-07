// The header of the Tasks page: search, the Open / Mine / Closed pills, the Filters popover (agent and states), list or tree, select (phone), keys and New task.
import { CLOSED, LISTED, TASK_STATES, type TaskColumn, type TaskFilter } from '@handline/app-core';
import { Icon, Popup, Segmented, Select } from './ui/index.ts';
import { STATE_GLYPH } from './taskui.tsx';

/** Which tasks the pills show: the open ones, the ones I own, or the closed ones. */
export type Scope = 'open' | 'mine' | 'closed';

export const HEADS: { col: TaskColumn; label: string; cls: string }[] = [
  { col: 'number', label: '#', cls: 'c-num' },
  { col: 'priority', label: 'P', cls: 'c-pri' },
  { col: 'state', label: 'state', cls: 'c-state' },
  { col: 'title', label: 'title', cls: 'c-title' },
  { col: 'timeout', label: 'time', cls: 'c-time' },
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
    <header className="tasks-head">
      <h1>Tasks</h1>
      <label className="tsearch" title="Search by number, title or agent">
        <Icon name="search" label="Search" size={14} aria-hidden="true" />
        <input
          type="search"
          value={props.query}
          placeholder="Search"
          aria-label="Search tasks"
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
      <span className="tfilters">
        <button
          className={`btn icon${props.filtering ? ' on' : ''}`}
          onClick={() => props.onAct('states')}
          aria-label={changed(filter) ? 'Filters, on' : 'Filters'}
          aria-expanded={props.filtering}
          title="Filters (v)"
        >
          <Icon name="filter" label="Filters" aria-hidden="true" />
          {changed(filter) ? <span className="tdot" title="A filter is on" /> : null}
        </button>
        <Popup open={props.filtering} onClose={() => props.onAct('states')} title="Filters">
          <div className="tfilter-body">
            <Select
              label="Agent"
              value={filter.agent}
              onChange={(e) => props.onFilter({ ...filter, agent: e.target.value })}
              options={[{ value: '', label: 'all agents' }, ...props.agents.map((a) => ({ value: a, label: a }))]}
            />
            {TASK_STATES.filter((s) => !CLOSED.includes(s)).map((s) => (
              <label key={s} className="tcheck">
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
            <button type="button" className="btn" onClick={() => props.onFilter({ ...filter, states: new Set(LISTED), agent: '' })}>
              Reset
            </button>
          </div>
        </Popup>
      </span>
      <span className="seg" role="group" aria-label="View">
        {(['list', 'tree'] as const).map((m) => (
          <button
            key={m}
            className={`btn icon${mode === m ? ' on' : ''}`}
            aria-pressed={mode === m}
            aria-label={m}
            title={`${m} (t)`}
            onClick={() => props.onView(m)}
          >
            <Icon name={m === 'list' ? 'viewList' : 'viewTree'} label={m} aria-hidden="true" />
          </button>
        ))}
      </span>
      <button
        className="btn icon select-btn"
        aria-pressed={selecting}
        aria-label="Select"
        title="Select rows"
        onClick={() => props.onSelecting()}
      >
        <Icon name="select" label="Select" aria-hidden="true" />
      </button>
      <button className="btn icon" onClick={() => props.onAct('keys')} aria-label="Keys" title="Keys (?)">
        <Icon name="keys" label="Keys" aria-hidden="true" />
      </button>
      <button className="btn icon primary" onClick={() => props.onAct('new')} aria-label="New task" title="New task (n)">
        <Icon name="plus" label="New task" aria-hidden="true" />
      </button>
    </header>
  );
}

type Can = { ok: boolean; why: string | undefined };

/** The icon bar under the table while rows are marked: it acts on them. One row's own actions are on the row. */
export function TasksBar(props: {
  marked: number;
  can: { discard: Can; edit: Can; merge: Can };
  onAct: (what: 'discard' | 'owner' | 'priority' | 'merge') => void;
}) {
  const { can } = props;
  return (
    <div className="tbar" role="toolbar" aria-label="Actions on the marked tasks">
      <span className="muted">{props.marked} marked</span>
      <button
        className="btn icon"
        onClick={() => props.onAct('discard')}
        disabled={!can.discard.ok}
        title={can.discard.why ?? 'Discard (d)'}
        aria-label="Discard"
      >
        <Icon name="dismiss" label="Discard" aria-hidden="true" />
      </button>
      <button
        className="btn icon"
        onClick={() => props.onAct('owner')}
        disabled={!can.edit.ok}
        title={can.edit.why ?? 'Owner (o)'}
        aria-label="Owner"
      >
        <Icon name="owner" aria-hidden="true" />
      </button>
      <button
        className="btn icon"
        onClick={() => props.onAct('priority')}
        disabled={!can.edit.ok}
        title={can.edit.why ?? 'Priority (p)'}
        aria-label="Priority"
      >
        <Icon name="priority" aria-hidden="true" />
      </button>
      <button
        className="btn icon"
        onClick={() => props.onAct('merge')}
        disabled={!can.merge.ok}
        title={can.merge.why ?? 'Merge (m): mark tasks, put the cursor on the one to keep'}
        aria-label="Merge"
      >
        <Icon name="merge" aria-hidden="true" />
      </button>
    </div>
  );
}
