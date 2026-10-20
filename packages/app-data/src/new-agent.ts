// The new-agent form, for the PC and the phone: its fields, the efforts to pick from, and the agent.create params the fields make.
import { useState } from 'react';
import type { Repo } from '@handline/client';
import { useAgents, useRepos } from '#src/read.ts';
import { useWrite } from '#src/write.ts';

export const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const;
type Effort = (typeof EFFORTS)[number];

export interface NewAgentFields {
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
export const EMPTY_AGENT_FIELDS: NewAgentFields = { name: '', role: '', repo: '', base: '', model: '', effort: '', parent: '', task: '', box: '2h' };

/** The params of agent.create: the name carries the manager's prefix, the branch defaults to the repo's own. */
export function createParams(f: NewAgentFields, repo: Pick<Repo, 'path' | 'default_branch'>) {
  return {
    name: (f.parent ? `${f.parent}.` : '') + f.name.trim(),
    role: f.role.trim(),
    repo: repo.path,
    base: f.base.trim() || repo.default_branch,
    ...(f.model.trim() ? { model: f.model.trim() } : {}),
    ...(f.effort ? { effort: f.effort as Effort } : {}),
    ...(f.parent ? { parent: f.parent } : {}),
  };
}

/** The form's state and its Create: `onCreated` hears of the operation the daemon started and of the fields it was started with. */
export function useNewAgentForm(onCreated: (operation: string, f: NewAgentFields) => void) {
  const create = useWrite('agent.create');
  const repos = useRepos();
  const agents = useAgents().data?.agents ?? [];
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
  return { create, repos, agents, f, setF, set, list, repo, prefix, ready, send };
}
