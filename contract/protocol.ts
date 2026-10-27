// Generated from contract/spec/ by contract/generate.py; do not edit.
/* eslint-disable */
import type { Role } from './gen/core.ts';
import { CORE_METHODS, type CoreMethods } from './gen/core.ts';
import { ACCOUNTS_METHODS, type AccountsMethods } from './gen/accounts.ts';
import { AGENTS_METHODS, type AgentsMethods } from './gen/agents.ts';
import { CHAT_METHODS, type ChatMethods } from './gen/chat.ts';
import { DEVICES_METHODS, type DevicesMethods } from './gen/devices.ts';
import { REPOS_METHODS, type ReposMethods } from './gen/repos.ts';
import { TASKS_METHODS, type TasksMethods } from './gen/tasks.ts';
import { TEAM_METHODS, type TeamMethods } from './gen/team.ts';
import { TERMINAL_METHODS, type TerminalMethods } from './gen/terminal.ts';

export * from './gen/core.ts';
export * from './gen/accounts.ts';
export * from './gen/agents.ts';
export * from './gen/chat.ts';
export * from './gen/devices.ts';
export * from './gen/repos.ts';
export * from './gen/tasks.ts';
export * from './gen/team.ts';
export * from './gen/terminal.ts';

export interface Methods extends CoreMethods, AccountsMethods, AgentsMethods, ChatMethods, DevicesMethods, ReposMethods, TasksMethods, TeamMethods, TerminalMethods {}

export type MethodName = keyof Methods;

export const METHODS: { [K in MethodName]: { role: Role; callers: Role[] | null; write: boolean; since: number } } = {
  ...CORE_METHODS,
  ...ACCOUNTS_METHODS,
  ...AGENTS_METHODS,
  ...CHAT_METHODS,
  ...DEVICES_METHODS,
  ...REPOS_METHODS,
  ...TASKS_METHODS,
  ...TEAM_METHODS,
  ...TERMINAL_METHODS,
};
