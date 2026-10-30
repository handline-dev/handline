// The header of the Tasks page: Open / Mine / Closed, search, Filter, View (List or Graph) and + New task; and the floating bar that acts on the ticked rows.
import type { ReactNode } from 'react';
import { CLOSED, LISTED, TASK_STATES, type TaskColumn, type TaskFilter } from '@handline/app-core';
import { Filter, Plus, ScanLine } from 'lucide-react';
import { BarInput, Button as OldButton, Icon, Kbd, PageHead, PageTitle, Popup, Select, Check } from '#src/ui/index.ts';
import { Button } from '#src/ds/Button.tsx';
import { Segmented } from '#src/ds/Field.tsx';
import { STATE_GLYPH } from '#src/taskui.tsx';
import { canCapture, requestCapture } from '#src/Capture.tsx';

/** Which tasks the pills show: the open ones, the ones I own, or the closed ones. */
export type Scope = 'open' | 'mine' | 'closed';

/** What the `s` key steps through. */
export const SORTS: TaskColumn[] = ['number', 'priority', 'state', 'title', 'owner', 'idle', 'timeout', 'asker'];

/** True when the filters differ from what a list opens with. */
const changed = (f: TaskFilter): boolean => !!f.agent || f.states.size !== LISTED.length || LISTED.some((s) => !f.states.has(s));

export function TasksHead(props: {
  agents: string[];
  filter: TaskFilter;
  view: 'list' | 'graph';
  scope: Scope;
  onScope: (s: Scope) => void;
  filtering: boolean;
  query: string;
  onQuery: (q: string) => void;
  onFilter: (f: TaskFilter) => void;
  onView: (v: 'list' | 'graph') => void;
  onAct: (what: 'states' | 'new') => void;
}) {
  const { filter } = props;
  return (
    <PageHead className="tasks-head flex-nowrap">
      <PageTitle className="mr-auto shrink-0">
        Tasks <Kbd className="ui-keys">J K</Kbd>
      </PageTitle>
      <label className="tsearch relative inline-flex items-center" title="Search by number, title or agent">
        <Icon name="search" label="Search" size={14} aria-hidden="true" className="pointer-events-none absolute left-1.5 text-ink-3" />
        <BarInput
          type="search"
          value={props.query}
          placeholder="Search"
          aria-label="Search tasks"
          className="w-[200px] min-w-0 pl-6 max-[1100px]:w-[120px] max-sm:w-full"
          onChange={(e) => props.onQuery(e.target.value)}
        />
      </label>
      <Segmented
        label="Show"
        value={props.scope}
        onChange={props.onScope}
        choices={[
          { value: 'open', label: 'Open' },
          { value: 'mine', label: 'Mine' },
          { value: 'closed', label: 'Closed' },
        ]}
      />
      <span className="tfilters relative inline-flex">
        <Button
          label="Filter"
          icon={Filter}
          title="Filter (v)"
          className="max-sm:min-h-[44px]"
          aria-expanded={props.filtering}
          onClick={() => props.onAct('states')}
        >
          Filter{changed(filter) ? ' · on' : ''}
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
                <Check
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
            <OldButton
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
        value={props.view}
        onChange={props.onView}
        choices={[
          { value: 'list', label: 'List' },
          { value: 'graph', label: 'Graph' },
        ]}
      />
      {canCapture() ? (
        <Button
          label="Capture a window"
          icon={ScanLine}
          title="Capture a window (⌘⇧C)"
          className="max-sm:min-h-[44px]"
          onClick={requestCapture}
        >
          Capture
        </Button>
      ) : null}
      <Button
        kind="primary"
        label="New task"
        icon={Plus}
        title="New task (n)"
        className="flex-none max-sm:min-h-[44px]"
        onClick={() => props.onAct('new')}
      >
        <span className="max-[1100px]:hidden">New task</span>
      </Button>
    </PageHead>
  );
}

type Can = { ok: boolean; why: string | undefined };

/** The bar floating at the bottom while rows are ticked: it acts on them, in words. */
export function TasksBar(props: {
  marked: number;
  can: { edit: Can; merge: Can };
  onAct: (what: 'owner' | 'priority' | 'merge') => void;
  onClear: () => void;
  children?: ReactNode;
}) {
  const { can } = props;
  const btn = 'max-sm:min-h-[44px]';
  return (
    <div
      role="toolbar"
      aria-label="Actions on the selected tasks"
      className="tbar fixed bottom-6 left-1/2 z-10 flex -translate-x-1/2 items-center gap-2 rounded-md border border-line-strong bg-raised px-3 py-2 shadow-lg"
    >
      <strong className="mr-1 whitespace-nowrap font-medium">{props.marked} selected</strong>
      <OldButton
        label="Assign"
        title={can.edit.why ?? 'Assign (o)'}
        disabled={!can.edit.ok}
        className={btn}
        onClick={() => props.onAct('owner')}
      />
      <OldButton
        label="Priority"
        title={can.edit.why ?? 'Priority (p)'}
        disabled={!can.edit.ok}
        className={btn}
        onClick={() => props.onAct('priority')}
      />
      <OldButton
        label="Merge"
        title={can.merge.why ?? 'Merge (m): tick tasks, put the cursor on the one to keep'}
        disabled={!can.merge.ok}
        className={btn}
        onClick={() => props.onAct('merge')}
      />
      {props.children}
      <OldButton label="Clear" className={btn} onClick={props.onClear} />
    </div>
  );
}
