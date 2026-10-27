// Generated from contract/spec/ by contract/generate.py; do not edit.
/* eslint-disable */
import type { Accepted, AgentName, ReadCursor, RequestId, Role, Timestamp } from './core.ts';

export type Window = {
  used_pct: number;
  resets_at: Timestamp | null;
  /** Where the number comes from, e.g. measured or estimated. */
  source: string;
};

export type Account = {
  name: string;
  login_state: "ok" | "needs_login" | "unknown";
  five_hour: Window | null;
  weekly: Window | null;
  /** The agents on this account (AgentView.account): running on it, or at rest with their prompt cache alive. One past its cache life is on none. */
  agents: AgentName[];
  moves: {
    at: Timestamp;
    agent: AgentName;
    from: string | null;
    to: string;
    reason: string;
  }[];
  /** The name screens show; the account's own name until an alias is set. */
  label: string;
  /** The account's own cap: most agents mid-turn at once; null follows active_cap. */
  cap: number | null;
  /** The cap in force: cap, else the active_cap setting; shrunk where an account's burn would reach a window's wall before it resets, and, with pacing on, by the account's pace (Account.pace). */
  cap_effective: number;
  /** Whether a dead usage read may spend one real turn to fix itself (hl account recover). */
  recover: boolean;
  cycle: {
    /** One-month subscription; off is renewing. */
    on: boolean;
    end: string | null;
    /** No end day is typed: end is null, and the account ends only when its login drops. */
    end_is_default: boolean;
  };
  /** Only with accounts.list `roster: true`. Agents on this account (Account.agents), running or at rest with their prompt cache alive (the figure `hl account list` prints). */
  agents_assigned?: number;
  /** Only with accounts.list `roster: true`. Those of them that are mid-turn or otherwise running. */
  agents_running?: number;
  /** Only with accounts.list `roster: true`. Agents whose config cannot be read and so count on no account; the same on every account, and every figure above is a floor while it is above 0. */
  agents_unplaced?: number;
  /** The login in progress or the last one to end; null when none was started. Reported on the accounts topic as account.changed. Never carries the authorize URL: only the founder's account.login reply has it. */
  login?: {
    login_id: string;
    state: "waiting" | "code_sent" | "done" | "failed" | "cancelled";
    /** Why the login ended failed, one plain line (a wrong code, or the account is logged in as the same Anthropic account as <names>); absent while it has not failed. */
    reason?: string;
  } | null;
  /** Only with accounts.list `usage: true`. The plan claude reports (`max`, `pro`), null when not recorded. */
  plan?: string | null;
  /** Only with accounts.list `usage: true`. The shortest and, when the vendor has one, the longest window: the figure read, the one that may be acted on, and where it came from. */
  windows?: {
    window: string;
    minutes: number;
    /** The figure as read. */
    pct: number | null;
    /** The figure the daemon acts on. */
    effective: number | null;
    /** Where the figure comes from, or why it is not trusted. */
    source: string;
    /** Unix seconds the window resets. */
    reset: number | null;
  }[];
  /** Only with accounts.list `usage: true`. Context used by the account's last session, percent. */
  ctx_pct?: number | null;
  /** Only with accounts.list `usage: true`. Seconds since the usage was read. */
  age_s?: number | null;
  /** Only with accounts.list `usage: true`. How long the account lasts at its recent burn, in words (`~10m to wall`), as `hl account list` prints it. */
  runway?: string;
  /** Only with accounts.list `usage: true`. Whether one more agent may start on it. */
  assignable?: boolean;
  /** Only with accounts.list `usage: true`. Why assignable is what it is. */
  assign_reason?: string;
  /** Set while the account is STALLED: a turn on it ended on an error that says the account itself cannot be used (a refused login, an org that disallows it, a hold or billing problem), or on one error the daemon does not name four times with no good turn between. No agent is placed on it; account.wake, a login or a good turn there ends it. null while it is usable. Absent from a daemon that does not send it. */
  stalled?: {
    /** The line a row shows beside STALLED, as an agent's stall does: `login expired` for authentication_failed, else the error itself. */
    reason: string;
    /** The StopFailure error that stalled it, as the CLI gave it. */
    error: string;
    /** The turn that showed it, in words. */
    detail: string;
    since: Timestamp;
  } | null;
  /** Only with accounts.list `usage: true`. The account's weekly pace (settings pacing, pace_target_pct, pace_burst_pct), judged on its last weekly reading, which is kept until its weekly reset even when it is stale. null when it has no weekly reading or its reset is not known; a null pace does not shrink the cap. back_at: for state over, when the line will have caught up to within pace_burst_pct of used_pct at no further use, or the end of its week when the line never gets there; else null. */
  pace?: {
    /** behind: used_pct is at or under line_pct, and the cap is not shrunk for pace. ahead: over the line by at most pace_burst_pct points; the pace cap is ceil(C x (line_pct + pace_burst_pct - used_pct) / pace_burst_pct), at least 1, where C is cap, else the active_cap setting. over: further ahead; the pace cap is 0 and the account starts no new turn. off: the pacing setting is off; the figures are still given. cap_effective is the smallest of C, the wall-cutoff cap and the pace cap. */
    state: "behind" | "ahead" | "over" | "off";
    /** Where the line is now: pace_target_pct x (now - start) / (end - start), where start is its weekly reset less 7 days and end is that reset or the subscription end, whichever comes first. */
    line_pct: number;
    /** The weekly window's used percent the pace is judged on: the last weekly reading, kept until its reset. */
    used_pct: number;
    /** Points of the week per hour the account may use from now to reach pace_target_pct at the end of its week: (pace_target_pct - used_pct) / hours left; 0 at or past the target. */
    rate_pct_h: number;
    back_at: Timestamp | null;
  } | null;
};

export type Stats = {
  window: "24h" | "7d";
  since: Timestamp;
  until: Timestamp;
  tasks_done: number;
  working_seconds: { [key: string]: number };
  moves: number;
  handoffs: number;
  blocks: number;
  per_account: {
    account: string;
    five_hour_used_pct: number | null;
    weekly_used_pct: number | null;
  }[];
};

export type Outlook = {
  range: "12h" | "24h" | "3d" | "7d" | "14d" | "30d";
  now: Timestamp;
  /** How many accounts have a reading. */
  accounts: number;
  /** What usage samples recorded, oldest first. */
  past: {
    at: Timestamp;
    left_pct: number | null;
  }[];
  /** A guess. With pacing on, each account's week goes at the smaller of its pace (Account.pace rate_pct_h, which ends at pace_target_pct) and its use over the last day; with pacing off, at its use over the last day. A week that resets starts again from nothing used. An account whose last reading is stale or at a wall keeps that reading until its reset and is not left out. Kept apart from `past`, never joined to it. */
  future: {
    at: Timestamp;
    left_pct: number | null;
  }[];
  runway: {
    account: string;
    runs_out_in_seconds: number | null;
    week_resets_at: Timestamp | null;
  }[];
  /** Oldest first; a day with no calls is absent. */
  cost_per_day: {
    /** Local calendar day, YYYY-MM-DD. */
    day: string;
    /** List-price cost in US dollars of each agent that day. */
    agents: { [key: string]: number };
  }[];
  /** What each agent used over the last `range` (the span of `past`), largest cost first, ties by name. A client groups agents by the first part of the dotted name; a group's numbers are the sums of its members' (est_pct null if any member's is). Absent from a daemon that does not send it. */
  agent_use?: {
    agent: string;
    /** Tokens the agent used in the span. */
    tokens: number;
    /** Its cost in US dollars at list price in the span, from the token log. */
    cost: number;
    /** Its cost as a fraction (0 to 1) of everyone's cost in the span; 0 when nothing was spent. */
    share: number;
    /** An estimate of the points of the combined week the agent used in the span: its cost share of each account times the points that account used, averaged over the accounts. An estimate, because what the plan charges is not what the price list says. Null when no account has a reading. */
    est_pct: number | null;
    /** Its cost share of each account in the span, by account name (a fraction, 0 to 1). An account on which nothing at all was spent is absent; one on which others spent but none of this agent's tokens was priced is 0. */
    fracs: { [key: string]: number };
  }[];
  /** Only with `agent`: the points of the combined week the pick had used by each time of `past`, one number per `past` entry in the same order: its cost share of each account times what that account had used so far, averaged over the accounts. Empty when the pick used nothing in the span or is not an agent or group. Absent from a daemon that does not send it. */
  overlay?: number[];
  /** Each account that has a reading in the span, by name: its windows and its pace line, for a chart per account. It replaces stats.headroom. Absent from a daemon that does not send it. */
  by_account?: {
    account: string;
    /** What usage samples recorded, at the same points as `past`. */
    past: {
      at: Timestamp;
      five_left: number | null;
      week_left: number | null;
      line_left: number | null;
    }[];
    /** The guess of `future` for this account, at the same points. The 5h window starts again from nothing at each reset, so its line is a saw; it goes at its use over the last day, and with pacing on at no more than the paced week rate converted to the 5h window by the account's measured ratio of weekly to 5h use. */
    future: {
      at: Timestamp;
      five_left: number | null;
      week_left: number | null;
      line_left: number | null;
    }[];
    /** Resets in the span, oldest first: those samples recorded, then those guessed. */
    resets: {
      at: Timestamp;
      window: "5h" | "7d";
      /** Only on a weekly (7d) reset the samples recorded: the percent of that week left unused at the last sample before it. Absent for a 5h reset, a guessed one, or a week with no sample. */
      unused_pct?: number;
    }[];
  }[];
};

export type Headroom = {
  now: Timestamp;
  step_s: number;
  combined: {
    /** Points up to now, oldest first. */
    past: {
      at: Timestamp;
      left_pct: number | null;
    }[];
    /** Points after now: a guess, kept apart from `past`. An account with no recent reading is left out. */
    future: {
      at: Timestamp;
      left_pct: number | null;
    }[];
  };
  /** Accounts that have a reading in the samples, by name. */
  accounts: {
    account: string;
    /** What usage samples recorded, at the same points as `combined.past`. */
    past: {
      at: Timestamp;
      five_left: number | null;
      week_left: number | null;
    }[];
    /** A guess: each window keeps its pace (the 5h one over the last day, the weekly one over this week) and starts again from nothing at its reset, so the 5h line is a saw. Null when the account's last reading is stale; 0 after an account ends. */
    future: {
      at: Timestamp;
      five_left: number | null;
      week_left: number | null;
    }[];
    /** A guess: the percent of this account's week still unused when it resets, if it goes on using the week at the rate of the last 24 hours (never below 0). Null when the week cannot be read or is over, or the last day has under 2 hours of readings to take a rate from. */
    left_at_reset?: number | null;
    /** The percent of this account's unused week that even full 5h windows from now to its reset could not use, at the account's measured ratio of weekly to 5h use (never below 0). Null when left_at_reset is, or the ratio is unknown. */
    lost_anyway?: number | null;
    /** Resets in the span, oldest first: those samples recorded, then those guessed (a 5h window opens again at its reset only while it is used). */
    resets: {
      at: Timestamp;
      window: "5h" | "7d";
      /** Only on a weekly (7d) reset the samples recorded: the percent of that week left unused at the last sample before it. Absent for a 5h reset, a guessed one, or a week with no sample. */
      unused_pct?: number;
    }[];
  }[];
  runs_out_at: Timestamp | null;
  next_reset: {
    at: Timestamp;
    account: string;
    window: "5h" | "7d";
  } | null;
};

export interface AccountsMethods {
  "accounts.list": { params: {
  /** Add each account's agents_assigned, agents_running and agents_unplaced. Default false. */
  roster?: boolean;
  /** Add each account's plan, windows, ctx_pct, age_s, runway, assignable and assign_reason (what `hl account list` prints beside the windows), and pace. Default false. */
  usage?: boolean;
}; result: {
    accounts: Account[];
  } & ReadCursor };
  "status.pick": { params: { [key: string]: unknown }; result: {
    /** The account to place the next agent on; null when none may take work (fail-closed). */
    picked: string | null;
    candidates: {
      account: string;
      reason: string;
    }[];
    /** One line per agent kind: its price per minute of the 5h window and what the price rests on. */
    agent_costs: string[];
  } };
  "account.set": { params: {
  account: string;
  /** Alias, one plain line; empty clears. */
  label?: string;
  cap?: number;
  recover?: boolean;
  /** The one-month subscription switch (hl account cycle on|off); off keeps the end day. */
  cycle?: boolean;
  /** Last usable day YYYY-MM-DD; a date alone turns the cycle on, as `hl account cycle --end` does; null clears it. */
  cycle_end?: string | null;
  request_id: RequestId;
}; result: Accepted };
  "account.wake": { params: {
  account: string;
  request_id: RequestId;
}; result: Accepted };
  "account.add": { params: {
  /** The new account's name: one plain line, no slash, not starting with a dot. */
  account: string;
  /** The tool the account is for; omitted means the default (claude). */
  vendor?: string;
  request_id: RequestId;
}; result: Accepted & {
    /** The name as registered. */
    account: string;
  } };
  "account.login": { params: {
  account: string;
  request_id: RequestId;
}; result: Accepted & {
    login_id: string;
    state: "waiting";
    /** The authorize URL the claude CLI printed; the person opens it and brings the code back with account.login_code. */
    url: string;
  } };
  "account.login_cancel": { params: {
  login_id: string;
  request_id: RequestId;
}; result: Accepted };
  "account.login_code": { params: {
  login_id: string;
  /** One-time; written to the CLI's stdin and nowhere else. */
  code: string;
  request_id: RequestId;
}; result: Accepted };
  "account.remove": { params: {
  account: string;
  /** Change nothing: answer `plan` and `would_remove`. A dry run needs no `confirm`. */
  dry_run?: boolean;
  /** The account's name, as the founder typed it; required unless dry_run. */
  confirm?: string;
  request_id: RequestId;
}; result: Accepted & {
    /** Dry run only: whether the removal would go ahead. */
    would_remove?: boolean;
    /** Dry run only: what it would do, line by line, or the one line that says why it would refuse. */
    plan?: string[];
  } };
  "stats.get": { params: {
  window: "24h" | "7d";
}; result: Stats };
  "stats.headroom": { params: {
  /** The first point. Default: 3 days before now. */
  from?: Timestamp;
  /** No point after this. Default: 3 days after now. After `from`. */
  to?: Timestamp;
  /** Seconds between points, from `from` on. Default 900. At most 2000 points. */
  step_s?: number;
}; result: Headroom };
  "stats.outlook": { params: {
  /** Headroom: that long back and as long ahead. Cost per day: the last that many days (under 1 day counts as 1). */
  range: "12h" | "24h" | "3d" | "7d" | "14d" | "30d";
  /** Also give `overlay` for this pick: an agent's name, or `grp:<name>` for the group of agents whose dotted names start with `<name>.` (and the agent `<name>` itself), the key of a row of the agents table a client draws. It rides on this call and not on a method of its own because the curve is read against `past` and must be sampled at the same moment as it; one pick costs one array, not one per agent. */
  agent?: string;
}; result: Outlook };
}

export const ACCOUNTS_METHODS: { [K in keyof AccountsMethods]: { role: Role; callers: Role[] | null; write: boolean; since: number } } = {
  "accounts.list": { role: "agent", callers: null, write: false, since: 1 },
  "status.pick": { role: "agent", callers: null, write: false, since: 1 },
  "account.set": { role: "founder", callers: null, write: true, since: 1 },
  "account.wake": { role: "founder", callers: null, write: true, since: 1 },
  "account.add": { role: "founder", callers: null, write: true, since: 1 },
  "account.login": { role: "founder", callers: null, write: true, since: 1 },
  "account.login_cancel": { role: "founder", callers: null, write: true, since: 1 },
  "account.login_code": { role: "founder", callers: null, write: true, since: 1 },
  "account.remove": { role: "founder", callers: null, write: true, since: 1 },
  "stats.get": { role: "member", callers: null, write: false, since: 1 },
  "stats.headroom": { role: "member", callers: null, write: false, since: 1 },
  "stats.outlook": { role: "member", callers: null, write: false, since: 1 },
};
