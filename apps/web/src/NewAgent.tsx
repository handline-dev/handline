// New agent (founder): the form of docs/design/new-agent-worktrees.md §1.2 as a sheet at `#/new`, then its progress at `#/new/:operation`.
// One agent.create per click; the form's fields stay in this component, so a failed creation returns to them as they were.
import { useState } from 'react';
import { classify, sentence, useAgents, useRepos, useServes, useWrite } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { Button, Dialog, Field, Icon, Select, Textarea } from './ui/index.ts';
import { goNew, useNewAgentRoute } from './route.ts';
import { forgetFirstTask, keepFirstTask, NewAgentProgress } from './NewAgentProgress.tsx';

const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const;
type Effort = (typeof EFFORTS)[number];
const ACCOUNT_NOTE = "A new agent's account is chosen when it starts; none can be named yet.";

export interface Fields {
  name: string;
  role: string;
  repo: string;
  base: string;
  model: string;
  effort: string;
  parent: string;
  task: string;
  box: string;
}
const EMPTY: Fields = { name: '', role: '', repo: '', base: '', model: '', effort: '', parent: '', task: '', box: '2h' };

/** The sidebar's button; only for the founder, and only on a daemon that lists agent.create. */
export function NewAgentButton() {
  const founder = useStore().client.hello?.role === 'founder';
  const { served } = useServes('agent.create');
  if (!founder || !served) return null;
  return (
    <Button variant="outline" icon="plus" label="New agent" onClick={() => goNew()}>
      New agent
    </Button>
  );
}

export function NewAgent({ onClose }: { onClose: () => void }) {
  const route = useNewAgentRoute();
  const store = useStore();
  const founder = store.client.hello?.role === 'founder';
  const { served } = useServes('agent.create');
  const create = useWrite('agent.create');
  const repos = useRepos();
  const agents = useAgents().data?.agents ?? [];
  const [f, setF] = useState<Fields>(EMPTY);
  const set = (k: keyof Fields, v: string) => setF((was) => ({ ...was, [k]: v }));
  if (!founder)
    return (
      <Dialog title="New agent" onClose={onClose}>
        <p>New agents are for the founder.</p>
      </Dialog>
    );
  if (!served)
    return (
      <Dialog title="New agent" onClose={onClose}>
        <p>This daemon cannot create agents.</p>
      </Dialog>
    );
  if (route.operation)
    return (
      <NewAgentProgress
        operation={route.operation}
        fields={f}
        onClose={onClose}
        onAnother={() => (setF(EMPTY), forgetFirstTask(), goNew())}
      />
    );
  const list = repos.data?.repos ?? [];
  const repo = list.find((r) => r.path === f.repo);
  const prefix = f.parent ? `${f.parent}.` : '';
  const ready = !!f.name.trim() && !!f.role.trim() && !!repo;
  const send = () => {
    if (!ready || create.isPending) return;
    create.mutate(
      {
        name: prefix + f.name.trim(),
        role: f.role.trim(),
        repo: repo.path,
        base: f.base.trim() || repo.default_branch,
        ...(f.model.trim() ? { model: f.model.trim() } : {}),
        ...(f.effort ? { effort: f.effort as Effort } : {}),
        ...(f.parent ? { parent: f.parent } : {}),
      },
      {
        onSuccess: (r) => {
          const operation = (r as { operation: string }).operation;
          keepFirstTask(operation, f.task, f.box);
          goNew(operation);
        },
      },
    );
  };
  const refused = create.isError ? classify(create.error) : null;
  return (
    <Dialog
      title="New agent"
      onClose={onClose}
      action={{ label: 'Create', keys: '⌘↵', form: 'newagent-form', disabled: !ready || create.isPending }}
    >
      <form
        id="newagent-form"
        name="newagent-form"
        className="tform na-form grid gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        {refused ? (
          <p className="ui-note bad" role="alert">
            <Icon name="failed" size={14} label="Error" />
            {sentence(refused)}
          </p>
        ) : null}
        <div className="tform-row grid grid-cols-2 gap-5 max-sm:grid-cols-1">
          <div className="na-name">
            {prefix ? <span className="muted mono">{prefix}</span> : null}
            <Field label="Name" value={f.name} onChange={(e) => set('name', e.target.value)} note="Plain text: no /, no leading ." />
          </div>
          <Field label="Role" list="na-roles" value={f.role} onChange={(e) => set('role', e.target.value)} />
          <datalist id="na-roles">
            {[...new Set(agents.map((a) => a.role).filter((r) => r && r.length <= 40))].map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
        </div>
        <div className="tform-row grid grid-cols-2 gap-5 max-sm:grid-cols-1">
          <div className="ui-field">
            <span className="ui-field-label">Repo</span>
            <Select
              label="Repo"
              value={f.repo}
              onChange={(e) =>
                setF((was) => ({ ...was, repo: e.target.value, base: list.find((r) => r.path === e.target.value)?.default_branch ?? '' }))
              }
              options={[
                { value: '', label: list.length ? 'pick a repo' : 'The daemon knows no repo.' },
                ...list.map((r) => ({ value: r.path, label: `${r.name} · ${r.path}` })),
              ]}
            />
          </div>
          <Field label="Base" value={f.base} onChange={(e) => set('base', e.target.value)} />
        </div>
        <div className="tform-row grid grid-cols-2 gap-5 max-sm:grid-cols-1">
          <Field label="Model" placeholder="default" value={f.model} onChange={(e) => set('model', e.target.value)} />
          <div className="ui-field">
            <span className="ui-field-label">Effort</span>
            <Select
              label="Effort"
              value={f.effort}
              onChange={(e) => set('effort', e.target.value)}
              options={[{ value: '', label: 'default' }, ...EFFORTS.map((e) => ({ value: e, label: e }))]}
            />
          </div>
        </div>
        <div className="tform-row grid grid-cols-2 gap-5 max-sm:grid-cols-1">
          <div className="ui-field">
            <span className="ui-field-label">Account</span>
            <Select label="Account" disabled value="" onChange={() => {}} options={[{ value: '', label: 'auto' }]} />
            <p className="ui-note">{ACCOUNT_NOTE}</p>
          </div>
          <div className="ui-field">
            <span className="ui-field-label">Parent</span>
            <Select
              label="Parent"
              value={f.parent}
              onChange={(e) => set('parent', e.target.value)}
              options={[{ value: '', label: 'none' }, ...agents.map((a) => ({ value: a.name, label: a.name }))]}
            />
          </div>
        </div>
        <div className="ui-field">
          <label htmlFor="na-task">First task (optional)</label>
          <Textarea id="na-task" className="bg-panel border-line-strong px-5 py-4 resize-y" rows={3} value={f.task} onChange={(e) => set('task', e.target.value)} />
        </div>
        {f.task.trim() ? <Field label="Time box" value={f.box} onChange={(e) => set('box', e.target.value)} /> : null}
        <Button label="Cancel" onClick={onClose} />
      </form>
    </Dialog>
  );
}
