// The app owns its daemon: when the Python service's socket or the Go gateway's socket is not answering at launch, the app starts what
// is missing as its own child processes and stops exactly those on quit. What was already running is never touched.

export interface Cmd {
  file: string;
  args: string[];
}

export interface Child {
  kill(): void;
  onExit(f: (code: number | null) => void): void;
}

export interface OwnerDeps {
  /** Whether something answers on the daemon's socket / the gateway's local socket. */
  socketUp(): Promise<boolean>;
  gatewayUp(): Promise<boolean>;
  spawn(cmd: Cmd): Child;
  python: Cmd;
  gateway: Cmd;
  wait(ms: number): Promise<void>;
  /** How long a started child gets to answer, in polls of 250 ms. */
  polls?: number;
}

export type DaemonState = 'starting' | 'running' | 'stopped' | 'failed';

export class Owner {
  state: DaemonState = 'stopped';
  /** What the tray shows next to a failure. */
  reason = '';
  private started: { name: string; child: Child }[] = [];
  private stopping = false;
  private listeners: (() => void)[] = [];

  private d: OwnerDeps;
  constructor(d: OwnerDeps) {
    this.d = d;
  }

  onChange(f: () => void): void {
    this.listeners.push(f);
  }

  private set(state: DaemonState, reason = ''): void {
    this.state = state;
    this.reason = reason;
    for (const f of this.listeners) f();
  }

  /** Brings both up, Python first (the gateway opens the database Python keeps). Never throws: a failure is the state. */
  async start(): Promise<void> {
    this.stopping = false;
    this.set('starting');
    try {
      await this.ensure('daemon', () => this.d.socketUp(), this.d.python);
      await this.ensure('gateway', () => this.d.gatewayUp(), this.d.gateway);
      this.set('running');
    } catch (e) {
      this.set('failed', e instanceof Error ? e.message : String(e));
    }
  }

  private async ensure(name: string, up: () => Promise<boolean>, cmd: Cmd): Promise<void> {
    if (await up()) return;
    const child = this.d.spawn(cmd);
    let exited: number | null | undefined;
    child.onExit((code) => {
      exited = code;
      this.started = this.started.filter((s) => s.child !== child);
      // A child that dies after the app came up is a failure the tray shows; one that dies because the app is quitting is not.
      if (this.state === 'running' && !this.stopping) this.set('failed', `the ${name} stopped (exit ${code ?? 'signal'})`);
    });
    this.started.push({ name, child });
    for (let i = 0; i < (this.d.polls ?? 120); i++) {
      if (exited !== undefined) throw new Error(`the ${name} exited at start (exit ${exited ?? 'signal'})`);
      if (await up()) return;
      await this.d.wait(250);
    }
    throw new Error(`the ${name} did not answer in time`);
  }

  /**
   * Moves a gateway this app started to another address. False, with `reason` in the tray, when the gateway is not this app's to move (it
   * kills nothing then) or the new address does not answer either.
   */
  async moveGateway(cmd: Cmd, answers: () => Promise<boolean>, reason: string): Promise<boolean> {
    if (this.owned().includes('gateway')) {
      await this.replaceGateway(cmd);
      if (this.state === 'running' && (await answers())) return true;
    }
    this.fail(reason);
    return false;
  }

  /** Shows a reason in the tray without touching what runs. */
  fail(reason: string): void {
    this.set('failed', reason);
  }

  /** Starts the gateway again with another command (another address), leaving the daemon alone. */
  async replaceGateway(cmd: Cmd): Promise<void> {
    this.set('starting');
    for (const s of this.started.filter((x) => x.name === 'gateway')) s.child.kill();
    this.started = this.started.filter((x) => x.name !== 'gateway');
    // Its socket goes when it exits; a new one must not mistake the old for itself.
    for (let i = 0; i < 40 && (await this.d.gatewayUp()); i++) await this.d.wait(250);
    this.d.gateway = cmd;
    try {
      await this.ensure('gateway', () => this.d.gatewayUp(), cmd);
      this.set('running');
    } catch (e) {
      this.set('failed', e instanceof Error ? e.message : String(e));
    }
  }

  /** Stops what this app started, the gateway before the daemon behind it. */
  stop(): void {
    this.stopping = true;
    for (const s of [...this.started].reverse()) s.child.kill();
    this.started = [];
    this.set('stopped');
  }

  /** Names of the processes this app started and still owns. */
  owned(): string[] {
    return this.started.map((s) => s.name);
  }
}

/** The two commands, from the environment: a checkout runs `bin/ocp daemon` and `go/handlined`; a packaged app names its own binaries. */
export function commands(
  env: (k: string) => string | undefined,
  root: string,
  listen: string,
  advertise: string | null = null,
): { python: Cmd; gateway: Cmd } {
  const homeArgs = env('HOME') ? ['-home', env('HOME')!] : ['-allow-real-home'];
  return {
    python: { file: env('OCP') ?? `${root}/bin/ocp`, args: ['daemon'] },
    gateway: {
      file: env('HANDLINED') ?? `${root}/go/handlined`,
      args: ['-gateway', ...homeArgs, '-listen', listen, ...(advertise ? ['-advertise', advertise] : [])],
    },
  };
}
