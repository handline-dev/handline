// The new-agent form, for the PC and the phone: its fields, the efforts to pick from, and the agent.create params the fields make.
import { useState } from 'react';
import type { AgentOptions, Repo } from '@handline/client';
import { useAgentOptions, useAgents, useRepos } from '#src/read.ts';
import { useWrite } from '#src/write.ts';

export const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const;
type Effort = (typeof EFFORTS)[number];

/** What the form offers to pick from. Without `agent.options` the efforts are today's list and the rest is free text (models) or absent. */
export interface NewAgentChoices {
  /** Whether the daemon sent its options; false falls back to the fixed efforts and free-text model. */
  fromDaemon: boolean;
  efforts: readonly string[];
  /** The models to pick from, or null for free text. */
  models: string[] | null;
  /** Every account with its login state; `offered` is false for one that is not logged in. Empty when the daemon sent none. */
  accounts: { name: string; login_state: string; offered: boolean }[];
  roles: AgentOptions['roles'];
  freeNames: string[];
}
export function shapeChoices(o: AgentOptions | undefined): NewAgentChoices {
  if (!o) return { fromDaemon: false, efforts: EFFORTS, models: null, accounts: [], roles: [], freeNames: [] };
  return {
    fromDaemon: true,
    efforts: o.efforts,
    models: o.models,
    accounts: o.accounts.map((a) => ({ ...a, offered: a.login_state === 'ok' })),
    roles: o.roles,
    freeNames: o.free_names,
  };
}

export interface NewAgentFields {
  name: string;
  role: string;
  repo: string;
  base: string;
  model: string;
  effort: string;
  account: string;
  parent: string;
  task: string;
  box: string;
}
export const EMPTY_AGENT_FIELDS: NewAgentFields = { name: '', role: '', repo: '', base: '', model: '', effort: '', account: '', parent: '', task: '', box: '2h' };

/** The params of agent.create: the name carries the manager's prefix, the branch defaults to the repo's own. */
export function createParams(f: NewAgentFields, repo: Pick<Repo, 'path' | 'default_branch'>) {
  return {
    name: (f.parent ? `${f.parent}.` : '') + f.name.trim(),
    role: f.role.trim(),
    repo: repo.path,
    base: f.base.trim() || repo.default_branch,
    ...(f.model.trim() ? { model: f.model.trim() } : {}),
    ...(f.effort ? { effort: f.effort as Effort } : {}),
    ...(f.account ? { account: f.account } : {}),
    ...(f.parent ? { parent: f.parent } : {}),
  };
}

/** The form's state and its Create: `onCreated` hears of the operation the daemon started and of the fields it was started with. */
export function useNewAgentForm(onCreated: (operation: string, f: NewAgentFields) => void) {
  const create = useWrite('agent.create');
  const repos = useRepos();
  const agents = useAgents().data?.agents ?? [];
  const choices = shapeChoices(useAgentOptions().data);
  const [f, setF] = useState<NewAgentFields>(EMPTY_AGENT_FIELDS);
  const set = (k: keyof NewAgentFields, v: string) => setF((was) => ({ ...was, [k]: v }));
  const list = repos.data?.repos ?? [];
  const repo = list.find((r) => r.path === f.repo);
  const prefix = f.parent ? `${f.parent}.` : '';
  const ready = !!f.name.trim() && !!f.role.trim() && !!repo;
  const send = () => {
    if (!repo || !ready || create.isPending) return;
    create.mutate(createParams(f, repo), { onSuccess: (r) => onCreated((r as { operation: string }).operation, f) });
  };
  return { create, repos, agents, choices, f, setF, set, list, repo, prefix, ready, send };
}
