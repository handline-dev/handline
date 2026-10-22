// New agent (founder): the form of docs/design/new-agent-worktrees.md §1.2 as a sheet at `#/new`, then its progress at `#/new/:operation`.
// One agent.create per click; the form's fields stay in this component, so a failed creation returns to them as they were.
import { EFFORTS, EMPTY_AGENT_FIELDS, classify, sentence, useNewAgentForm, useServes } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { Button, Dialog, Field, Icon, Select, Textarea, NOTE, FIELD, FIELD_LABEL } from '#src/ui/index.ts';
import { NAV } from '#src/NavRows.tsx';
import { goNew, useNewAgentRoute } from '#src/route.ts';
import { forgetFirstTask, keepFirstTask, NewAgentProgress } from '#src/NewAgentProgress.tsx';

const ACCOUNT_NOTE = "A new agent's account is chosen when it starts; none can be named yet.";

/** The sidebar's button; only for the founder, and only on a daemon that lists agent.create. */
export function NewAgentButton() {
  const founder = useStore().client.hello?.role === 'founder';
  const { served } = useServes('agent.create');
  if (!founder || !served) return null;
  return (
    <div className="px-1.5 pb-1">
      <button className={NAV} onClick={() => goNew()}>
        <Icon name="plus" size={14} aria-hidden /> New agent
      </button>
    </div>
  );
}

export function NewAgent({ onClose }: { onClose: () => void }) {
  const route = useNewAgentRoute();
  const store = useStore();
  const founder = store.client.hello?.role === 'founder';
  const { served } = useServes('agent.create');
  const { create, agents, f, setF, set, list, prefix, ready, send } = useNewAgentForm((operation, made) => {
    keepFirstTask(operation, made.task, made.box);
    goNew(operation);
  });
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
        onAnother={() => (setF(EMPTY_AGENT_FIELDS), forgetFirstTask(), goNew())}
      />
    );
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
        className="tform na-form grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        {refused ? (
          <p className={`${NOTE} text-bad`} role="alert">
            <Icon name="failed" size={14} label="Error" />
            {sentence(refused)}
          </p>
        ) : null}
        <div className="tform-row grid grid-cols-2 gap-3 max-sm:grid-cols-1">
          <div className="na-name flex min-w-0 items-end gap-1">
            {prefix ? <span className="muted mono">{prefix}</span> : null}
            <Field
              className="flex-1"
              label="Name"
              value={f.name}
              onChange={(e) => set('name', e.target.value)}
              note="Plain text: no /, no leading ."
            />
          </div>
          <Field label="Role" list="na-roles" value={f.role} onChange={(e) => set('role', e.target.value)} />
          <datalist id="na-roles">
            {[...new Set(agents.map((a) => a.role).filter((r) => r && r.length <= 40))].map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
        </div>
        <div className="tform-row grid grid-cols-2 gap-3 max-sm:grid-cols-1">
          <div className={FIELD}>
            <span className={FIELD_LABEL}>Repo</span>
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
        <div className="tform-row grid grid-cols-2 gap-3 max-sm:grid-cols-1">
          <Field label="Model" placeholder="default" value={f.model} onChange={(e) => set('model', e.target.value)} />
          <div className={FIELD}>
            <span className={FIELD_LABEL}>Effort</span>
            <Select
              label="Effort"
              value={f.effort}
              onChange={(e) => set('effort', e.target.value)}
              options={[{ value: '', label: 'default' }, ...EFFORTS.map((e) => ({ value: e, label: e }))]}
            />
          </div>
        </div>
        <div className="tform-row grid grid-cols-2 gap-3 max-sm:grid-cols-1">
          <div className={FIELD}>
            <span className={FIELD_LABEL}>Account</span>
            <Select label="Account" disabled value="" onChange={() => {}} options={[{ value: '', label: 'auto' }]} />
            <p className={NOTE}>{ACCOUNT_NOTE}</p>
          </div>
          <div className={FIELD}>
            <span className={FIELD_LABEL}>Parent</span>
            <Select
              label="Parent"
              value={f.parent}
              onChange={(e) => set('parent', e.target.value)}
              options={[{ value: '', label: 'none' }, ...agents.map((a) => ({ value: a.name, label: a.name }))]}
            />
          </div>
        </div>
        <div className={FIELD}>
          <label htmlFor="na-task">First task (optional)</label>
          <Textarea id="na-task" className="bg-panel border-line-strong px-3 py-2 resize-y" rows={3} value={f.task} onChange={(e) => set('task', e.target.value)} />
        </div>
        {f.task.trim() ? <Field label="Time box" value={f.box} onChange={(e) => set('box', e.target.value)} /> : null}
        <Button label="Cancel" onClick={onClose} />
      </form>
    </Dialog>
  );
}
