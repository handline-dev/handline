// The fake daemon's chat: messages with landing state, the transcript and the working line, on the chat topic.
// A sent message lands one tick at a time (typed, delivered, then the agent's turn and its reply) so a UI sees every state; `tick` is driven by the daemon's own.
import { Failure, invalidParams, refused } from '#src/errors.ts';
import type { Attachment, ImagePart, TextPart, TranscriptItem } from '@handline/contract';
import { deliveryOf, receiptPatch, uuidFor, type Msg } from '#src/delivery.ts';
import { ChatFailure, ChatStore, IDLE } from '#src/chatstore.ts';

export { ChatFailure };

/** What a message carries: FakeAttachments.message. */
type Images = (body: string, ids: unknown, task: unknown) => { attachments: Attachment[]; parts?: (TextPart | ImagePart)[] };

export class FakeChat extends ChatStore {
  send(p: Record<string, unknown>, person: string, images: Images = () => ({ attachments: [] })) {
    const agent = String(p.agent),
      body = p.body;
    const parentItem = this.replyItem(p);
    if (typeof body !== 'string') throw new ChatFailure('bad_request', 'a message needs a body');
    const { attachments, parts } = images(body, p.attachments, p.task);
    if (!body.trim() && !attachments.length) throw new ChatFailure('bad_request', 'a message needs a body');
    const open = this.sessions.get(agent)?.state !== 'parked';
    const delivery = deliveryOf(p);
    const s = this.sessions.get(agent);
    const receipt = s?.structured
      ? {
          state: this.thread(agent).working.turn_running && delivery === 'queue' ? ('queued' as const) : ('sent' as const),
          uuid: uuidFor(String(p.request_id)),
        }
      : undefined;
    const at = this.now();
    const id = this.seed(agent, {
      body,
      from: person,
      to: agent,
      at,
      parent: typeof p.parent === 'number' ? p.parent : null,
      ...(parentItem ? { parent_item: parentItem } : {}),
      ...(attachments.length ? { attachments } : {}),
      ...(parts ? { parts } : {}),
      landing: 'queued',
      delivered_at: null,
      typed_at: null,
      landed_at: null,
      sending_reason: open ? null : 'the agent has no open session',
    });
    if (receipt) Object.assign(this.find(id), receiptPatch({ ...this.find(id), receipt }, receipt.state, at));
    this.emit('chat.message', agent, this.view(this.find(id)));
    if (receipt?.state === 'sent') this.steps.push(() => this.settle(id, 'confirmed'));
    if (open && !receipt)
      this.steps.push(
        () => this.type(id),
        () => this.land(id),
        () => this.think(id),
        () => this.reply(id),
      );
    return { request: id, ...(receipt && { receipt: this.find(id).receipt }) };
  }
  /** chat.send's parent_item: a reply of this agent's transcript; the stored quote is its first 300 code points. */
  private replyItem(p: Record<string, unknown>): { id: number; text: string } | null {
    if (p.parent_item === undefined) return null;
    const id = p.parent_item;
    if (typeof id !== 'number' || !Number.isInteger(id)) throw invalidParams('parent_item must be an item id', 'parent_item');
    if (p.parent !== undefined || p.task !== undefined) throw invalidParams('parent_item goes with neither parent nor task', 'parent_item');
    const item = this.thread(String(p.agent)).items.find((i) => i.id === id);
    if (!item) throw new Failure('not_found', `no item ${id}`, { what: 'item' });
    if (item.kind !== 'reply') throw invalidParams('parent_item must be a reply', 'parent_item');
    return { id, text: [...(item.text ?? '')].slice(0, 300).join('') };
  }
  private live(id: number) {
    const m = this.messages.find((x) => x.id === id);
    return m && m.landing !== 'withdrawn' ? m : undefined;
  }
  private type(id: number) {
    const m = this.live(id);
    if (m) this.change(m, { landing: 'typed', state: 'typed', typed_at: this.now() });
  }
  private land(id: number) {
    const m = this.live(id);
    if (m) this.change(m, { landing: 'delivered', state: 'delivered', landed_at: this.now(), delivered_at: this.now() });
  }
  /** The agent's turn runs or ends without any message of the person to answer (another agent's message, a task's): only the working line moves. */
  setWorking(agent: string, running: boolean, line: string | null = null) {
    const t = this.thread(agent);
    t.working = running ? { turn_running: true, line, since: this.now() } : { ...IDLE };
    this.emit('chat.working', agent, t.working);
  }
  /** The agent starts its turn: the working line shows and a tool runs. */
  private think(id: number) {
    const m = this.live(id);
    if (!m) return;
    const t = this.thread(m.agent);
    t.working = { turn_running: true, line: 'Reading the code', since: this.now() };
    this.emit('chat.working', m.agent, t.working);
    this.item(m.agent, { kind: 'tool', tool: 'Read', text: 'src/main.ts' });
  }
  /** The turn ends with a reply. */
  private reply(id: number) {
    const m = this.live(id);
    if (!m) return;
    this.item(m.agent, { kind: 'reply', text: `Got it: "${m.body}"` });
    const t = this.thread(m.agent);
    t.working = { ...IDLE };
    this.emit('chat.working', m.agent, t.working);
  }
  private item(agent: string, item: Omit<TranscriptItem, 'id' | 'at'>) {
    const t = this.thread(agent);
    const full: TranscriptItem = { id: this.nextItem++, at: this.now(), ...item };
    t.items.push(full);
    this.emit('chat.transcript', agent, full);
  }

  /** Types a queued message now, ahead of the scripted order. */
  sendNow(p: Record<string, unknown>) {
    const m = this.find(p.request);
    if (m.landing !== 'queued') throw new ChatFailure('refused', 'that message is not waiting', 'state');
    if (m.receipt) return this.write(m.id);
    this.type(m.id);
    return {};
  }
  /** The person's own undo: a message not yet delivered is taken back; a delivered one is closed. */
  cancel(p: Record<string, unknown>) {
    const m = this.find(p.request);
    if (this.sessions.has(m.from) || m.from === m.to) throw new ChatFailure('refused', 'that message is not yours', 'state');
    if (m.landing === 'withdrawn') throw new ChatFailure('refused', 'that message is closed', 'state');
    // The agent already has it: closing it tells the agent to stop, so the caller must say it knows.
    const written = m.receipt ? m.receipt.state !== 'queued' : m.landing === 'delivered';
    if (written && p.confirm !== true)
      throw new ChatFailure('refused', 'the agent already has this message; cancelling tells it to stop', 'confirm');
    this.change(m, { landing: 'withdrawn', state: 'withdrawn' });
  }
  /** A queued message of a structured agent goes to the CLI: receipt sent, then the CLI's echo on the next step. */
  private write(id: number) {
    const m = this.find(id);
    this.change(m, receiptPatch(m as Msg, 'sent', this.now()));
    this.steps.push(() => this.settle(id, 'confirmed'));
    return { receipt: m.receipt };
  }
  private settle(id: number, state: 'confirmed' | 'unknown') {
    const m = this.live(id);
    if (m?.receipt?.state === 'sent') this.change(m, receiptPatch(m as Msg, state, this.now()));
  }
  /** A structured session's state moved: its turn starts or ends (one signal for the working line and for what queues), and a session that closed loses what was not echoed. */
  stateMoved(agent: string, from: string, to: string): void {
    if (!this.sessions.get(agent)?.structured) return;
    const turn = (w: string) => w === 'running' || w === 'waiting';
    if (!turn(from) && turn(to)) this.setTurn(agent, true);
    else if (turn(from) && !turn(to)) this.setTurn(agent, false, to === 'idle');
    if (to === 'parked' || to === 'no_terminal') this.lose(agent);
  }
  /** The turn of `agent` starts or ends: chat.working shows it, and a message sent now queues behind it. An end that is a finished turn (`done`) writes the next held message. */
  setTurn(agent: string, running: boolean, done = true): void {
    const t = this.thread(agent);
    t.working = running ? { turn_running: true, line: 'Working', since: this.now() } : { ...IDLE };
    this.emit('chat.working', agent, t.working);
    if (!running && done) this.turnEnded(agent);
  }
  /** A turn of `agent` ended: the oldest message held behind it is written. */
  turnEnded(agent: string): void {
    const next = this.messages.find((m) => m.agent === agent && m.receipt?.state === 'queued' && m.landing !== 'withdrawn');
    if (next) this.write(next.id);
  }
  /** The daemon or the CLI of `agent` ended: what was written and not echoed is unknown, and is never written again. */
  lose(agent: string): void {
    for (const m of this.messages) if (m.agent === agent) this.settle(m.id, 'unknown');
  }
  /** The stored reply of a retried chat.send with its receipt as it is now; a withdrawn operation is refused. */
  refreshed(r: Record<string, unknown>): Record<string, unknown> {
    const m = r.receipt ? this.messages.find((x) => x.id === r.request) : undefined;
    if (!m) return r;
    if (m.landing === 'withdrawn') throw refused('state', 'that message was withdrawn');
    return { ...r, receipt: m.receipt };
  }
  /** One scripted step; false when none is waiting. */
  tick(): boolean {
    const s = this.steps.shift();
    if (s) s();
    return !!s;
  }
}
