// `pnpm dev:team <team>` (dev-team.ts): the page and the shell against a team's dev instance (`hl dev on <team>`), never the live daemon.
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';

/** What `hl dev on` writes to ~/.handline-dev/<team>/instance.json, as far as the app reads it. */
export interface Instance {
  team: string;
  home: string;
  branch: string;
  sha: string;
  socket: string;
  gateway: string;
}

export const instanceFile = (userHome: string, team: string): string => path.join(userHome, '.handline-dev', team, 'instance.json');

/** The team's instance, or why there is none to run against. */
export function readInstance(file: string, team: string): Instance | string {
  let text: string;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return `${team} has no dev instance: \`hl dev on ${team}\` first`;
  }
  const i = JSON.parse(text) as Partial<Instance>;
  if (!i.home || !i.socket || !i.gateway) return `${file} names no home, socket or gateway: \`hl dev on ${team}\` again`;
  return i as Instance;
}

/** Whether something accepts a connection: a Unix socket path, or host:port. */
export const answers = (where: string): Promise<boolean> =>
  new Promise((resolve) => {
    const [host, port] = where.split(':');
    const c = where.includes('/') ? net.createConnection(where) : net.createConnection(Number(port), host);
    c.setTimeout(3000, () => (c.destroy(), resolve(false)));
    c.once('connect', () => (c.destroy(), resolve(true)));
    c.once('error', () => (c.destroy(), resolve(false)));
  });

/** Why the instance cannot be run against now, null when its daemon and its gateway both answer. */
export async function offReason(i: Instance, up: (where: string) => Promise<boolean> = answers): Promise<string | null> {
  for (const [what, where] of [
    ['daemon', i.socket],
    ['gateway', i.gateway],
  ] as const)
    if (!(await up(where))) return `${i.team}'s dev instance is off (its ${what} does not answer on ${where}): \`hl dev on ${i.team}\``;
  return null;
}

/** The environment the page and the shell run with: every HANDLINE_ and OCP_ variable dropped, so nothing names the live home or its
 * gateway, then the instance's. Connect mode attaches to the instance and never starts a daemon of its own on that home. */
export function teamEnv(base: NodeJS.ProcessEnv, i: Instance, devUrl: string): NodeJS.ProcessEnv {
  const env = Object.fromEntries(Object.entries(base).filter(([k]) => !k.startsWith('HANDLINE_') && !k.startsWith('OCP_')));
  return {
    ...env,
    HANDLINE_HOME: i.home,
    HANDLINE_DATA: path.join(i.home, 'app-data'),
    HANDLINE_GATEWAY: i.gateway,
    HANDLINE_GATEWAY_WS: i.gateway,
    HANDLINE_MODE: 'connect',
    HANDLINE_DEV_URL: devUrl,
  };
}

/** A loopback port free now, for the team's vite server: two teams' pages then run side by side. */
export const freePort = (): Promise<number> =>
  new Promise((resolve, reject) => {
    const s = net.createServer().once('error', reject);
    s.listen(0, '127.0.0.1', () => {
      const { port } = s.address() as net.AddressInfo;
      s.close(() => resolve(port));
    });
  });
