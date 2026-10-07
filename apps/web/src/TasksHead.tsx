// The header of the Tasks page: the agent filter, list or tree, sort, states, closed, select (phone), keys and New task.
import type { TaskColumn, TaskFilter } from '@handline/app-core';

export const HEADS: { col: TaskColumn; label: string; cls: string }[] = [
  { col: 'number', label: '#', cls: 'c-num' },
  { col: 'priority', label: 'P', cls: 'c-pri' },
  { col: 'state', label: 'state', cls: 'c-state' },
  { col: 'title', label: 'title', cls: 'c-title' },
  { col: 'owner', label: 'owner', cls: 'c-owner' },
  { col: 'idle', label: 'idle', cls: 'c-idle' },
  { col: 'timeout', label: 'timeout', cls: 'c-time' },
  { col: 'asker', label: 'asker', cls: 'c-asker' },
];
export const SORTS = HEADS.map((h) => h.col);

type Mode = 'list' | 'tree';

export function TasksHead(props: {
  agents: string[];
  filter: TaskFilter;
  mode: Mode;
  sort: TaskColumn;
  selecting: boolean;
  onFilter: (f: TaskFilter) => void;
  onView: (m: Mode) => void;
  onSort: (by: TaskColumn) => void;
  onSelecting: () => void;
  onAct: (what: 'states' | 'keys' | 'new') => void;
}) {
  const { filter, mode, sort, selecting } = props;
  return (
    <header className="tasks-head">
      <h1>Tasks</h1>
      <label className="tfield">
        <span className="vh">Agent</span>
        <select value={filter.agent} onChange={(e) => props.onFilter({ ...filter, agent: e.target.value })} aria-label="Agent">
          <option value="">all agents</option>
          {props.agents.map((a) => (
            <option key={a}>{a}</option>
          ))}
        </select>
      </label>
      <span className="seg" role="group" aria-label="View">
        {(['list', 'tree'] as const).map((m) => (
          <button key={m} className={`btn${mode === m ? ' on' : ''}`} aria-pressed={mode === m} onClick={() => props.onView(m)}>
            {m}
          </button>
        ))}
      </span>
      <label className="tfield">
        <span className="vh">Sort</span>
        <select value={sort} onChange={(e) => props.onSort(e.target.value as TaskColumn)} aria-label="Sort by">
          {HEADS.map((h) => (
            <option key={h.col} value={h.col}>
              sort: {h.label}
            </option>
          ))}
        </select>
      </label>
      <button className="btn" onClick={() => props.onAct('states')}>
        states
      </button>
      <label className="tcheck">
        <input type="checkbox" checked={filter.closed} onChange={(e) => props.onFilter({ ...filter, closed: e.target.checked })} /> closed
      </label>
      <button className="btn select-btn" aria-pressed={selecting} onClick={() => props.onSelecting()}>
        Select
      </button>
      <button className="btn" onClick={() => props.onAct('keys')} aria-label="Keys" title="Keys">
        ?
      </button>
      <button className="btn primary" onClick={() => props.onAct('new')}>
        + New task
      </button>
    </header>
  );
}
