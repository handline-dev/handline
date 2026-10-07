// HR proposals and the team: hr.list, hr.decide, hr.withdraw, team.get. Tests put proposals in with `script.hr.raise`.
import type { Proposal } from '@handline/contract';
import { badRequest, Failure } from './errors.ts';
import type { Call } from './handlers.ts';
import type { Host } from './script.ts';
import type { Session } from './session.ts';

const WAITING = new Set(['open', 'apply_failed']);
const digest = (p: Proposal) => `d:${p.id}:${p.diff.length}:${p.reason ?? ''}`;

export class FakeHr {
  readonly proposals: Proposal[] = [];
  /** Approving a proposal in this set fails to apply: it comes back apply_failed with this error. */
  readonly failApply = new Map<number, string>();
  private host: Host;
  constructor(host: Host) {
    this.host = host;
  }
  /** A proposal an agent raised: stored open and announced on the hr topic. */
  raise(p: Partial<Proposal> & { id: number; title: string; diff: string }): Proposal {
    const full: Proposal = {
      agent: 'handline.x',
      state: 'open',
      created: this.host.iso(),
      subject: '',
      requested_by: 'handline.architect',
      ...p,
    };
    full.subject ||= digest(full);
    this.proposals.push(full);
    this.host.emit('hr', 'hr.raised', { agent: full.agent, title: full.title, data: { proposal: full } });
    return full;
  }
  settle(p: Proposal, state: string, error: string | null = null) {
    p.state = state;
    p.error = error;
    p.settled = this.host.iso();
    this.host.emit('hr', 'hr.settled', { agent: p.agent, title: p.title, data: { proposal: p } });
  }
  find(id: unknown): Proposal {
    const p = this.proposals.find((x) => x.id === id);
    if (!p) throw new Failure('not_found', 'no such proposal', { what: 'proposal' });
    return p;
  }
}

const gate = (g: string, msg: string, proposal: Proposal) => new Failure('refused', msg, { reason: msg, gate: g, proposal });

export const HR_HANDLERS: Record<string, (h: Call) => unknown> = {
  'hr.list': (h) => ({ proposals: h.script.hr.proposals }),
  'hr.decide': (h) => {
    const { p, script } = h;
    const hr = script.hr;
    const prop = hr.find(p.proposal);
    if (p.decision !== 'approve' && p.decision !== 'reject') throw badRequest('decision is approve or reject', 'decision');
    if (p.decision === 'reject' && !(typeof p.note === 'string' && p.note.trim())) throw badRequest('a rejection needs a note', 'note');
    if (!WAITING.has(prop.state)) throw gate('proposal_settled', `the proposal is already ${prop.state}`, prop);
    if (typeof p.subject === 'string' && p.subject !== prop.subject)
      throw gate('proposal_changed', 'the proposal changed after you read it', prop);
    if (p.decision === 'reject') hr.settle(prop, 'rejected');
    else if (hr.failApply.has(prop.id)) hr.settle(prop, 'apply_failed', hr.failApply.get(prop.id));
    else hr.settle(prop, 'approved');
    return script.accepted('Decided.');
  },
  'hr.withdraw': (h) => {
    const { p, script } = h;
    const prop = script.hr.find(p.proposal);
    if (!WAITING.has(prop.state)) throw gate('proposal_settled', `the proposal is already ${prop.state}`, prop);
    script.hr.settle(prop, 'withdrawn');
    return script.accepted('Withdrawn.');
  },
};

/** team.get: every agent with its role and files, and the edges: a parent and its child may ask each other. */
export function teamOf(sessions: Session[], at: string) {
  const agents = sessions.map((s) => ({
    name: s.fx.name,
    role: s.fx.role,
    parent: s.fx.parent,
    role_file: { path: `${s.fx.worktree}/ROLE.md`, updated: at },
    base_file: { path: `${s.fx.worktree}/base.md`, updated: null },
  }));
  const edges = sessions.flatMap((s) =>
    s.fx.parent
      ? [
          { from: s.fx.parent, to: s.fx.name },
          { from: s.fx.name, to: s.fx.parent },
        ]
      : [],
  );
  return { agents, edges };
}
