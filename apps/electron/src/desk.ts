import { RpcError, type DaemonClient, type Event } from '@handline/client';
import { badgeText, noticeFor, suppressed, type Notice } from './notices.ts';

/** The part of the client the desk uses. */
export type Feed = Pick<DaemonClient, 'onEvent' | 'onGap' | 'onState' | 'subscribe' | 'call'>;

/** What the shell does with the daemon's news; Electron's side is injected so this runs under node. */
export interface Ports {
  setBadge(text: string): void;
  notify(n: Notice, onClick: () => void): void;
  screen(): { focused: boolean; agent: string | null };
  /** A notice was clicked: show the window, and take the page to the notice's target if it has one. */
  open(n: Notice): void;
}

/** Keeps the badge on the daemon's unread count and raises one native notice per notifications event. */
export class Desk {
  private client: Feed;
  private ports: Ports;
  private off: (() => void)[] = [];
  private seen = new Set<number>();
  constructor(client: Feed, ports: Ports) {
    this.client = client;
    this.ports = ports;
  }

  /** Safe to call again after it threw: the handlers are added once, the subscription is asked for again. */
  async start(): Promise<void> {
    if (!this.off.length) this.listen();
    await this.client.subscribe(['inbox', 'status', 'notifications']);
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

  stop(): void {
    for (const f of this.off) f();
    this.off = [];
  }

  async refresh(): Promise<void> {
    try {
      this.ports.setBadge(badgeText((await this.client.call('daemon.status', {})).inbox.unread));
    } catch {
      /* offline: the badge keeps the last count */
    }
  }

  private raise(e: Event): void {
    const n = noticeFor(e);
    if (!n || this.seen.has(e.cursor)) return;
    this.seen.add(e.cursor);
    if (suppressed(n, this.ports.screen())) return;
    this.ports.notify(n, () => this.ports.open(n));
  }
}
