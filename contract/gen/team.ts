// Generated from contract/spec/ by contract/generate.py; do not edit.
/* eslint-disable */
import type { Accepted, AgentName, ReadCursor, RequestId, Role, Timestamp } from './core.ts';

export type TeamAgent = {
  name: AgentName;
  role: string;
  parent: AgentName | null;
  role_file: {
    path: string;
    updated: Timestamp | null;
  };
  base_file: {
    path: string;
    updated: Timestamp | null;
  };
};

export type Proposal = {
  id: number;
  /** The agent the proposal is about, as the card heads it `<kind> <agent>`. For a bundle: `<N> changes`, not an agent's name. */
  agent: string;
  /** One line, the card's head: `<kind> <agent>`, with ` -> <to>` after it for link and unlink; a bundle's is `bundle <N> changes`. Text from agents' names; draw as text. */
  title: string;
  /** What approving does, in words, one change per line. Not a unified diff. */
  diff: string;
  /** open: waits for the founder. applying: approved by the founder and held until each agent in `waiting_on` ends its turn; the daemon then closes those sessions, applies the change and restarts each agent that stays; it turns approved once the change is applied (an agent that stays and does not start at once is `follow_up`, not a failure), or apply_failed when the change itself fails; `settled` stays null and the founder is offered no action meanwhile. approved: decided and applied. rejected. apply_failed: approved by the founder but not applied (the apply failed, or never ran): still the founder's move, and hr.decide approve again applies it. withdrawn: taken back by its author or the founder. An open set: a client draws a word it does not know as text, offers no action on it and does not count it. See hr.list for the Python store's own states. */
  state: string;
  /** Set only while state is applying: the agents whose running turn the change waits on, by their names before the change. A card names them. */
  waiting_on?: AgentName[];
  /** Set only while state is applying: when the proposal turned applying. With `waiting_on` it tells the card since when and why it waits (those agents' turns are running); hr.apply_now ends the wait. */
  waiting_since?: Timestamp;
  /** Set on an approved proposal whose change is applied but an agent it renamed is not running yet. Absent when the change needs no start or the start went through; it is removed when the start succeeds. The change is applied either way: a client never draws it as not applied or offers approve again. */
  follow_up?: {
    agent: AgentName;
    /** waiting: the start is put off for machine load and the daemon makes it by itself when the load drops; the card shows no retry. failed: the start failed for another reason and only the founder's Start now (agent.force_start) retries it. An open set: draw a word you do not know as text. */
    state: string;
    /** Why, from the daemon (the held limit or the failure), one line. */
    text: string;
  };
  created: Timestamp;
  /** Digest of what the proposal changes as drawn; hr.decide's approve hands it back. */
  subject: string;
  /** Who asked: an agent's name, or `human` for the founder's own change (a retire held for a running session, say); a client draws `human` as the founder, never as an agent. Text from the daemon's record; optional. */
  requested_by?: string;
  /** Why it asks, as the agent wrote it. Text only, from an agent: a client draws it as plain text, never as markup, and drops control characters. Optional. */
  reason?: string;
  /** The founder's note on the decision (why it was rejected, say), as hr.decide took it. Absent when the founder gave none or it is still open. */
  note?: string;
  /** The fields the proposal asks for (vendor, model, effort, team, repo, to, then each `set <key>`). Every value is text from an agent (same rule as `reason`); optional, and a key it did not ask for is absent. Absent for kind bundle: its `diff` carries the changes. members, repos, relation, response and context texts are in `diff` only. */
  asked?: {
    vendor?: string;
    model?: string;
    team?: string;
    repo?: string;
    /** rename: the new name; link, unlink: the other end. */
    to?: string;
    /** `set <key>`: each agent setting asked for, key to value. */
    sets?: { [key: string]: string };
    effort?: string;
  };
  error?: string | null;
  settled?: Timestamp | null;
  /** What the proposal does: add, retire, rename, link, unlink, team, settings, context, schedule or bundle (several changes decided once). An open set: a client draws a word it does not know as text. Optional until the Python hr.list sends it; a client then reads the first word of `title`. */
  kind?: string;
};

export type Schedule = {
  id: number;
  target: AgentName;
  cron: string;
  message: string;
  next_run: Timestamp | null;
  last_run: Timestamp | null;
  set_by: string;
};

/** What the proposal changes, by kind (hl hr). Every kind needs name. */
export type HrArgs = {
  /** The agent the proposal is about (the new agent's name for add; a team's manager for team). */
  name: string;
  team?: string;
  repo?: string;
  model?: string;
  effort?: string;
  vendor?: string;
  /** rename: the new name; link, unlink: the other end. */
  to?: string;
  /** team: each a suffix (`sea` is `name.sea`) or a full name. */
  members?: string[];
  /** team: repo by member. */
  repos?: { [key: string]: string };
  /** settings: agent setting keys as AgentSettingValues names them. context: whole new file texts keyed `ROLE.md` or `base.md`; empty text or control characters other than tab and newline is bad_request. */
  sets?: { [key: string]: string };
  /** bundle: the steps, at most 40, each judged against the roster the earlier ones leave. name is then ignored (send the count, `3 changes`). */
  changes?: { [key: string]: unknown }[];
  /** schedule: add a schedule to `name`, edit one of its (give was_cron for which; cron and message are then the new ones, a missing one stays), or remove one (was_cron). */
  action?: "add" | "edit" | "remove";
  /** schedule add, edit: five cron fields; it may not fire more than once an hour (bad_request). */
  cron?: string;
  /** schedule add, edit: the text typed at the agent each time, at most 1000 characters. */
  message?: string;
  /** schedule edit, remove: the cron of the existing schedule. */
  was_cron?: string;
  /** schedule edit, remove: its message, when two of the agent's schedules share the cron. */
  was_message?: string;
};

export type ScheduleError = {
  target: AgentName;
  /** Why the agent's `schedules` value cannot be read, as `hl schedule list` says it. */
  error: string;
};

export interface TeamMethods {
  "team.seal": { params: {
  /** Change nothing: answer `plan`. */
  dry_run?: boolean;
  request_id: RequestId;
}; result: {
    /** Always true: a refusal is an error. */
    ok: boolean;
    /** Dry run only: one line per file that would be sealed (its label, why, its digest), or that nothing needs sealing. */
    plan?: string;
    /** Without dry_run: one line per file sealed, or that nothing needed sealing. */
    said?: string;
  } };
  "hr.propose": { params: {
  kind: "add" | "retire" | "rename" | "link" | "unlink" | "team" | "settings" | "context" | "schedule" | "bundle";
  args: HrArgs;
  why?: string;
  request_id: RequestId;
}; result: Accepted & {
    proposal: number;
  } };
  "hr.withdraw": { params: {
  proposal: number;
  request_id: RequestId;
}; result: Accepted };
  "trust.propose": { params: {
  root: string;
  why?: string;
  request_id: RequestId;
}; result: Accepted & {
    proposal?: string;
  } };
  "trust.list": { params: {
  proposals?: boolean;
}; result: {
    roots: string[];
    /** Empty unless `proposals` is true. State is the store's word (proposed, applied, rejected...). */
    proposals: {
      id: string;
      root: string;
      state: string;
      /** The decision request that asked the founder. */
      request?: number;
      requested_by?: string;
    }[];
    /** The same roots as `roots`, in the same order, with why and who added each. */
    declared: {
      root: string;
      why: string;
      /** An agent's name, or human. */
      added_by: string;
    }[];
  } };
  "trust.remove": { params: {
  root: string;
  request_id: RequestId;
}; result: Accepted };
  "team.get": { params: { [key: string]: unknown }; result: {
    agents: TeamAgent[];
    edges: {
      from: string;
      to: string;
    }[];
  } };
  "hr.list": { params: {
  /** Only the proposals in that state; without it, the list below. */
  state?: string;
}; result: {
    proposals: Proposal[];
  } & ReadCursor };
  "hr.decide": { params: {
  proposal: number;
  decision: "approve" | "reject";
  note?: string;
  subject?: string;
  request_id: RequestId;
}; result: Accepted };
  "hr.apply_now": { params: {
  proposal: number;
  request_id: RequestId;
}; result: Accepted };
  "schedules.list": { params: { [key: string]: unknown }; result: {
    schedules: Schedule[];
    /** Present only when an agent's `schedules` value cannot be read: that agent, and why. */
    errors?: ScheduleError[];
  } };
  "schedule.add": { params: {
  target: AgentName;
  cron: string;
  message: string;
  request_id: RequestId;
}; result: Accepted & {
    schedule: number;
  } };
  "schedule.edit": { params: {
  schedule: number;
  target?: AgentName;
  cron?: string;
  message?: string;
  request_id: RequestId;
}; result: Accepted };
  "schedule.remove": { params: {
  schedule: number;
  request_id: RequestId;
}; result: Accepted };
}

export const TEAM_METHODS: { [K in keyof TeamMethods]: { role: Role; callers: Role[] | null; write: boolean; since: number } } = {
  "team.seal": { role: "founder", callers: null, write: true, since: 1 },
  "hr.propose": { role: "agent", callers: null, write: true, since: 1 },
  "hr.withdraw": { role: "agent", callers: null, write: true, since: 1 },
  "trust.propose": { role: "agent", callers: null, write: true, since: 1 },
  "trust.list": { role: "agent", callers: null, write: false, since: 1 },
  "trust.remove": { role: "founder", callers: null, write: true, since: 1 },
  "team.get": { role: "agent", callers: null, write: false, since: 1 },
  "hr.list": { role: "agent", callers: null, write: false, since: 1 },
  "hr.decide": { role: "member", callers: null, write: true, since: 1 },
  "hr.apply_now": { role: "member", callers: null, write: true, since: 1 },
  "schedules.list": { role: "agent", callers: null, write: false, since: 1 },
  "schedule.add": { role: "founder", callers: null, write: true, since: 1 },
  "schedule.edit": { role: "founder", callers: null, write: true, since: 1 },
  "schedule.remove": { role: "founder", callers: null, write: true, since: 1 },
};
