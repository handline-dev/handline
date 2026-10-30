// Follow-main mode: the app runs from its own checkout of main. It follows GitHub's main as the repo it was cloned from last fetched it
// (PRs merge on GitHub, so that repo's own `main` branch stands still), says so when main moves, and on the person's yes brings its checkout up to date, rebuilds what changed and relaunches.
import fs from 'node:fs';
import path from 'node:path';

export interface Rebuild {
  /** The lockfile changed: install before building. */
  install: boolean;
  web: boolean;
  /** The app's own main process, or what it bundles. */
  electron: boolean;
}

const starts = (f: string, ...dirs: string[]) => dirs.some((d) => f.startsWith(d));

/** What a list of changed paths means for the build. Docs and tests mean nothing. The daemon is built and restarted on every update, so `go/` is not asked about. */
export function decide(files: string[]): Rebuild {
  const r: Rebuild = { install: false, web: false, electron: false };
  for (const f of files) {
    if (f === 'package.json' || f === 'pnpm-lock.yaml' || f === 'pnpm-workspace.yaml') r.install = r.web = r.electron = true;
    if (starts(f, 'apps/electron/', 'packages/client/', 'packages/client-node/', 'packages/app-core/')) r.electron = true;
    if (starts(f, 'apps/web/', 'packages/', 'contract/')) r.web = true;
  }
  if (r.electron) r.web = true; // the relaunched app serves the page it was built with
  return r;
}

/** The page is built here beside the running one (apps/web/dist-next) and swapped in at the restart. */
export const WEB_NEXT = 'dist-next';

/** The main that counts: the source repo's `origin/main`, which is GitHub's main as of its last fetch. */
export const SOURCE_MAIN = 'origin/main';
/** Run in the app's checkout: takes the source repo's `origin/main` as its own `origin/main`. */
export const FETCH_SOURCE = ['fetch', 'origin', `+refs/remotes/${SOURCE_MAIN}:refs/remotes/${SOURCE_MAIN}`];

/** Where GitHub's main is now. `git` runs in the source repo; a fetch that fails (offline) leaves the last one standing. */
export async function peekMain(git: (...args: string[]) => Promise<string>): Promise<Main> {
  await git('fetch', 'origin', 'main').catch(() => '');
  return { sha: await git('rev-parse', SOURCE_MAIN), title: await git('log', '-1', '--format=%s', SOURCE_MAIN) };
}

export interface Main {
  sha: string;
  title: string;
}

/** `updating`: the checkout is being brought up to main and built; `ready`: it is built and waits for the restart. */
export type FollowState = 'current' | 'available' | 'updating' | 'ready' | 'failed';

export interface FollowDeps {
  /** Runs a command in the app's checkout and gives its output; rejects with the command's own words on a failure. */
  run(file: string, args: string[]): Promise<string>;
  /** Where GitHub's main is now, read through the repo the checkout was cloned from (`peekMain`). */
  peek(): Promise<Main>;
  /** Builds the daemon, moves the running one onto it and waits for it, for the commit `sha`; rejects with the reason. */
  upgradeDaemon(sha: string): Promise<void>;
  /** Removes a page built beside the running one that was left half done by a failed build. */
  clearWeb(): void;
  /** Puts the page built beside the running one in its place, just before the relaunch (the running page keeps its files until then). */
  swapWeb(): void;
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
  /** While updating: the step, "Building (2 of 3)", and how far along, 0 to 1. */
  step = '';
  fraction = 0;
  /** The command output so far, for "Details" after a failure. */
  log = '';
  /** The main the checkout was built at, once it is: the restart goes to this one. */
  private built: string | null = null;
  /** The commit the checkout was at when this build began, kept across a failed try so the retry still rebuilds what that change touched. */
  private from: string | null = null;
  private d: FollowDeps;
  private listeners: ((fresh: boolean) => void)[] = [];

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
    if (this.state === 'updating' || this.state === 'ready') return; // a built update waits for its restart
    try {
      const main = await this.d.peek();
      if (main.sha === this.sha) {
        this.next = null;
        this.set('current');
        return;
      }
      const fresh = main.sha !== this.next?.sha;
      this.next = { ...main };
      this.set('available', fresh);
    } catch (e) {
      this.fail(e);
    }
  }

  private async run(file: string, args: string[]): Promise<string> {
    this.log += `$ ${file} ${args.join(' ')}\n`;
    try {
      const out = await this.d.run(file, args);
      this.log += out;
      return out;
    } catch (e) {
      this.log += `${e instanceof Error ? e.message : String(e)}\n`;
      throw e;
    }
  }

  /** Brings the checkout up to main and builds what changed (the page beside the running one), without touching what runs. Ends `ready`. */
  async prepare(): Promise<void> {
    if (this.state === 'updating' || this.state === 'ready') return;
    this.log = '';
    this.built = null;
    this.progress('Preparing', 0);
    try {
      const target = (await this.d.peek()).sha;
      await this.run('git', FETCH_SOURCE);
      this.from ??= (await this.run('git', ['rev-parse', 'HEAD'])).trim();
      const head = this.from;
      const files = (await this.run('git', ['diff', '--name-only', head, target])).split('\n').filter(Boolean);
      await this.run('git', ['merge', '--ff-only', target]);
      this.d.clearWeb();
      const r = decide(files);
      // The electron build stamps the sha the daemon is compared with, so it runs every time.
      const steps: [string, string[]][] = [
        ...(r.install ? [['pnpm', ['install', '--frozen-lockfile']] as [string, string[]]] : []),
        ...(r.web ? [['pnpm', ['--filter', '@handline/web', 'build', '--outDir', WEB_NEXT]] as [string, string[]]] : []),
        ['pnpm', ['--filter', '@handline/electron', 'build']],
      ];
      for (const [i, [file, args]] of steps.entries()) {
        this.progress(`Building (${i + 1} of ${steps.length})`, i / steps.length);
        await this.run(file, args);
      }
      this.built = target;
      this.from = null;
      this.step = '';
      this.fraction = 1;
      this.set('ready');
    } catch (e) {
      this.fail(e);
    }
  }

  /** "Restart now": the daemon moves onto the built main, the built page takes its place, the app relaunches. */
  async restartNow(): Promise<void> {
    if (this.state === 'updating' || !this.built) return;
    this.progress('Restarting', 1);
    try {
      await this.d.upgradeDaemon(this.built.slice(0, 12));
      this.d.swapWeb();
      this.d.beforeRelaunch();
      this.d.relaunch();
    } catch (e) {
      this.fail(e);
    }
  }

  /** "Try again" after a failure: the restart when the build was done, else the whole preparation. */
  async retry(): Promise<void> {
    if (this.state !== 'failed') return;
    this.state = 'current';
    await (this.built ? this.restartNow() : this.prepare());
  }

  /** One step of the work: said to the listeners at once, so the notice moves. */
  private progress(step: string, fraction: number): void {
    this.step = step;
    this.fraction = fraction;
    this.set('updating');
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

/** Calls `onMove` (once, after a pause) when `origin/main` of the repo at `gitDir` changes, as any fetch there does: a loose ref is replaced, or packed-refs is rewritten. `refs` is the folder of the ref. */
export function watchMain(
  gitDir: string,
  onMove: () => void,
  onError: (e: Error) => void,
  pause = 400,
  refs = path.join('refs', 'remotes', 'origin'),
): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const ping = (name: string | Buffer | null) => {
    const n = name?.toString();
    if (n && n !== 'main' && n !== 'packed-refs' && n !== 'main.lock') return;
    clearTimeout(timer);
    timer = setTimeout(onMove, pause);
  };
  const watchers = [path.join(gitDir, refs), gitDir].flatMap((dir) => {
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
