import { myTurn } from '@handline/app-core';
import { RpcError, type DaemonClient, type Event } from '@handline/client';
import { badgeText, noticeFor, suppressed, type Notice } from '#src/notices.ts';

/** The part of the client the desk uses. */
export type Feed = Pick<DaemonClient, 'onEvent' | 'onGap' | 'onState' | 'subscribe' | 'call'>;

/** What the shell does with the daemon's news; Electron's side is injected so this runs under node. */
export interface Ports {
  /** `text` is the badge ('99+' past two digits), `count` the exact number the tray line shows. */
  setBadge(text: string, count: number): void;
  notify(n: Notice, onClick: () => void): void;
  screen(): { focused: boolean; agent: string | null };
  /** A notice was clicked: show the window, and take the page to the notice's target if it has one. */
  open(n: Notice): void;
}

/** The setting that turns off the desktop notice for each notifications event; an event not listed (an HR proposal) has no switch. */
const PUSH_KEY = {
  prompt_asked: 'push_prompt_asked',
  task_done: 'push_task_done',
  stall: 'push_stall',
  account_out: 'push_account_out',
} as const;

/** Keeps the badge on the My turn count and raises one native notice per notifications event. */
export class Desk {
  private client: Feed;
  private ports: Ports;
  private off: (() => void)[] = [];
  private seen = new Set<number>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  constructor(client: Feed, ports: Ports) {
    this.client = client;
    this.ports = ports;
  }

  /** Safe to call again after it threw: the handlers are added once, the subscription is asked for again. */
  async start(): Promise<void> {
    if (!this.off.length) this.listen();
    await this.client.subscribe(['inbox', 'status', 'notifications', 'agents', 'tasks']);
    // A daemon that does not serve hr yet refuses the topic; that must not take the badge and the other notices with it.
    await this.client.subscribe(['hr']).catch((e) => {
      if (!(e instanceof RpcError && e.code === 'bad_request')) throw e;
    });
    await this.refresh();
  }

  private listen(): void {
    this.off.push(
      this.client.onEvent((e) => {
        if (e.topic === 'inbox' || e.topic === 'status') void this.refresh();
        // An agent asking for the person and a task changing owner move the count too; these topics are chatty, so they wait out a burst.
        if (e.topic === 'agents' || e.topic === 'tasks') this.later();
        if (e.topic === 'notifications' || e.topic === 'hr') this.raise(e);
      }),
    );
    // A reconnect or a dropped history can have changed the count without an event.
    this.off.push(this.client.onGap(() => void this.refresh()));
    this.off.push(
      this.client.onState((s) => {
        if (s === 'open') void this.refresh();
      }),
    );
  }

  private later(): void {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.refresh(), 300);
  }

  stop(): void {
    clearTimeout(this.timer);
    for (const f of this.off) f();
    this.off = [];
  }

  async refresh(): Promise<void> {
    try {
      const [inbox, agents, tasks] = await Promise.all([
        this.client.call('inbox.list', {}),
        this.client.call('agents.list', {}),
        this.client.call('tasks.list', {}),
      ]);
      // The same list the My turn page draws; the person's own app is the founder.
      const n = myTurn({ items: inbox.items, agents: agents.agents, tasks: tasks.items, me: 'human' }).length;
      this.ports.setBadge(badgeText(n), n);
    } catch {
      /* offline: the badge keeps the last count */
    }
  }

  private raise(e: Event): void {
    const n = noticeFor(e);
    if (!n || this.seen.has(e.cursor)) return;
    this.seen.add(e.cursor);
    if (suppressed(n, this.ports.screen())) return;
    const key = PUSH_KEY[n.event as keyof typeof PUSH_KEY];
    const notify = () => this.ports.notify(n, () => this.ports.open(n));
    if (!key) return notify();
    void this.pushedOn(key).then((on) => on && notify());
  }

  /** Read at each notice, so a switch turned off in the page holds back the next one; a daemon that does not answer or has no key raises it as before. */
  private async pushedOn(key: (typeof PUSH_KEY)[keyof typeof PUSH_KEY]): Promise<boolean> {
    try {
      const values = (await this.client.call('settings.get', {})).values as Record<string, unknown> | undefined;
      return values?.[key] !== false;
    } catch {
      return true;
    }
  }
}
