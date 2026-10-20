// The new-agent form, for the PC and the phone: its fields, the efforts to pick from, and the agent.create params the fields make.
import type { Repo } from '@handline/client';

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
