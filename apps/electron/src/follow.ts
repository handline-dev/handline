// Follow-main mode: the app runs from its own checkout of main. It watches the main ref of the repo it was cloned from (the merger lands
// there), says so when main moves, and on the person's yes brings its checkout up to date, rebuilds what changed and relaunches.
import fs from 'node:fs';
import path from 'node:path';

export interface Rebuild {
  /** The lockfile changed: install before building. */
  install: boolean;
  /** The Go gateway changed. */
  gateway: boolean;
  web: boolean;
  /** The app's own main process, or what it bundles. */
  electron: boolean;
}

const starts = (f: string, ...dirs: string[]) => dirs.some((d) => f.startsWith(d));

/** What a list of changed paths means for the build. Docs and tests mean nothing. The app relaunches either way, which also restarts the daemon it owns. */
export function decide(files: string[]): Rebuild {
  const r: Rebuild = { install: false, gateway: false, web: false, electron: false };
  for (const f of files) {
    if (f === 'package.json' || f === 'pnpm-lock.yaml' || f === 'pnpm-workspace.yaml') r.install = r.web = r.electron = true;
    if (starts(f, 'apps/electron/', 'packages/client/', 'packages/client-node/', 'packages/app-core/')) r.electron = true;
    if (starts(f, 'apps/web/', 'packages/', 'contract/')) r.web = true;
    if (starts(f, 'go/')) r.gateway = true;
  }
  if (r.electron) r.web = true; // the relaunched app serves the page it was built with
  return r;
}

export interface Main {
  sha: string;
  title: string;
}

export type FollowState = 'current' | 'available' | 'updating' | 'failed';

export interface FollowDeps {
  /** Runs a command in the app's checkout and gives its output; rejects with the command's own words on a failure. */
  run(file: string, args: string[]): Promise<string>;
  /** Where main is now, read from the repo the checkout was cloned from. */
  peek(): Promise<Main>;
  /** Called before the relaunch: keeps what the window shows. */
  beforeRelaunch(): void;
  relaunch(): void;
}

export class Follower {
  state: FollowState = 'current';
  /** The commit the app runs. */
  sha = '';
  /** The newer main, while there is one. */
  next: Main | null = null;
  reason = '';
  private d: FollowDeps;
  private listeners: ((fresh: boolean) => void)[] = [];
  /** The main the person has said Later to: it is not offered again until main moves past it. */
  private declined = '';

  constructor(d: FollowDeps) {
    this.d = d;
  }

  /** Called on every change; `fresh` is true when a main the person has not yet answered has appeared. */
  onChange(f: (fresh: boolean) => void): void {
    this.listeners.push(f);
  }

  private set(state: FollowState, fresh = false): void {
    this.state = state;
    for (const f of this.listeners) f(fresh);
  }

  private git(...args: string[]): Promise<string> {
    return this.d.run('git', args).then((s) => s.trim());
  }

  async init(): Promise<void> {
    this.sha = await this.git('rev-parse', 'HEAD').catch(() => '');
    await this.look();
  }

  /** One look at where main is. Never throws. */
  async look(): Promise<void> {
    if (this.state === 'updating') return;
    try {
      const main = await this.d.peek();
      if (main.sha === this.sha) {
        this.next = null;
        this.set('current');
        return;
      }
      const fresh = main.sha !== this.declined && main.sha !== this.next?.sha;
      this.next = { ...main };
      this.set('available', fresh);
    } catch (e) {
      this.fail(e);
    }
  }

  /** "Later": the popover goes away and comes back when main next moves. */
  later(): void {
    if (this.next) this.declined = this.next.sha;
    this.set(this.state);
  }

  /** "Restart now": bring the checkout up to main, rebuild what changed, relaunch. */
  async restartNow(): Promise<void> {
    if (this.state === 'updating') return;
    this.set('updating');
    try {
      const target = (await this.d.peek()).sha;
      await this.git('fetch', 'origin', 'main');
      const head = await this.git('rev-parse', 'HEAD');
      const files = (await this.git('diff', '--name-only', head, target)).split('\n').filter(Boolean);
      await this.git('merge', '--ff-only', target);
      const r = decide(files);
      if (r.install) await this.d.run('pnpm', ['install', '--frozen-lockfile']);
      if (r.gateway) await this.d.run('go', ['build', '-C', 'go', '-o', 'handlined', './cmd/handlined']);
      if (r.web) await this.d.run('pnpm', ['--filter', '@handline/web', 'build']);
      if (r.electron) await this.d.run('pnpm', ['--filter', '@handline/electron', 'build']);
      this.d.beforeRelaunch();
      this.d.relaunch();
    } catch (e) {
      this.fail(e);
    }
  }

  /** The ref watch could not start or broke: the tray says so, and "Check again" still looks by hand. */
  watchFailed(e: Error): void {
    this.fail(new Error(`cannot watch main: ${e.message}`));
  }

  private fail(e: unknown): void {
    this.reason = e instanceof Error ? e.message.split('\n')[0]! : String(e);
    this.set('failed');
  }
}

/** Calls `onMove` (once, after a pause) when the main ref of the repo at `gitDir` changes: a loose ref is replaced, or packed-refs is rewritten. */
export function watchMain(gitDir: string, onMove: () => void, onError: (e: Error) => void, pause = 400): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const ping = (name: string | Buffer | null) => {
    const n = name?.toString();
    if (n && n !== 'main' && n !== 'packed-refs' && n !== 'main.lock') return;
    clearTimeout(timer);
    timer = setTimeout(onMove, pause);
  };
  const watchers = [path.join(gitDir, 'refs', 'heads'), gitDir].flatMap((dir) => {
    try {
      // fs.watch reports a lost folder or too many open files as an event later; with no listener that would end the process.
      return [fs.watch(dir, (_event, name) => ping(name)).on('error', onError)];
    } catch (e) {
      onError(e as Error);
      return [];
    }
  });
  return () => {
    clearTimeout(timer);
    for (const w of watchers) w.close();
  };
}
