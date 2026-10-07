// U10: the Inbox page on the fake daemon's inbox: skeleton, the cards of each kind, filters, the bell, the verbs and their refusals, the keys,
// deep links, and a daemon that does not serve the inbox.
import { act } from 'react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 300) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV => {
  const data = new Map<string, string>([['rail', '0']]);
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null) => el?.textContent ?? '';
const card = (id: number) => $(`[data-item="${id}"]`)!;
const button = (within: Element | Document | null, label: string) =>
  [...(within ?? box).querySelectorAll('button, a')].find((b) => (text(b).trim() || b.getAttribute('aria-label')) === label) as
    HTMLElement | undefined;
/** Opens the details of a row: its options and boxes live there. */
const expand = async (id: number) => {
  const b = button(card(id), 'Show details');
  if (b) await click(b);
};
const ids = () => $$('.inbox-card').map((c) => c.dataset.item);
const key = (k: string) => act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true })));

async function mount(hash = '#/inbox', s = setup()) {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

// Seeded by the fake: 1 result (read, task 7), 2 block (read), 3 decision, 4 prompt, 5 result (task 12); 3, 4 and 5 are unread and are
// the only cards: a read item leaves the list.
describe('the page', () => {
  it('shows the skeleton while loading, then the cards newest first with the unread count', async () => {
    const s = setup();
    s.daemon.knobs.latency(400);
    await mount('#/inbox', s);
    await until(() => $('.inbox .skeleton'), 'the skeleton');
    expect($('.inbox .skeleton')!.getAttribute('aria-busy')).toBe('true');
    await until(() => $$('.inbox-card').length === 3, 'the cards', 600);
    expect($('.inbox .skeleton')).toBeNull();
    expect(ids()).toEqual(['5', '4', '3']);
    expect($('.inbox-top h2')!.getAttribute('title')).toBe('Inbox');
    expect($('.inbox-top h2 [aria-label="3 unread"]')).not.toBeNull();
    expect(document.title).toBe('(3) Handline');
  });

  it('each kind shows its own verbs as icons: options and Answer on a decision, Reply on a result, none on a prompt', async () => {
    await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    expect(button(card(3), 'Yes')).toBeUndefined(); // a row is one line until it is opened
    await expand(3);
    expect([...card(3).querySelectorAll('button')].map((b) => text(b) || b.getAttribute('aria-label'))).toEqual([
      'Hide details',
      'Mark read',
      'Dismiss',
      'Yes',
      'No',
      'Answer',
    ]);
    await expand(5);
    expect(button(card(5), 'Reply')).toBeTruthy();
    await expand(4);
    expect(card(4).querySelector('[data-prompt-slot]')!.textContent).toBe('rm -rf build');
    expect(button(card(4), 'Reply')).toBeUndefined();
    expect(card(3).querySelector('.inbox-seen [data-icon="unread"]')).not.toBeNull(); // unread is a filled mark, read a hollow one
    expect(card(3).querySelector('.badge [data-icon="decision"]')!.getAttribute('aria-label')).toBe('Decision');
    expect(card(5).querySelector('.badge [data-icon="result"]')).not.toBeNull();
  });

  it('no card has an Open button: the card itself is the link', async () => {
    await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    expect($$('.inbox-card a')).toHaveLength(0);
    expect(card(5).classList.contains('go')).toBe(true);
  });

  it('evidence shows its first three lines and Show more shows the rest', async () => {
    const s = setup();
    s.daemon.inbox.add({ kind: 'result', title: 'Long', agent: 'handline.architect', evidence: 'a\nb\nc\nnothing else touched' });
    await mount('#/inbox', s);
    await until(() => $$('.inbox-card').length === 4, 'the cards');
    const c = $$('.inbox-card')[0]!;
    await click(button(c, 'Show details')!);
    expect(text(c.querySelector('pre'))).not.toContain('nothing else touched');
    await click(button(c, 'Show more'));
    expect(text(c.querySelector('pre'))).toContain('nothing else touched');
    expect(button(c, 'Show less')).toBeTruthy();
  });

  it('filters by kind icon and by agent, says when nothing matches, and Clear filters brings the list back', async () => {
    await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    const pickAgent = async (value: string) => {
      const sel = $('select[aria-label="Agent"]') as HTMLSelectElement;
      await act(async () => {
        const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!;
        set.call(sel, value);
        sel.dispatchEvent(new Event('change', { bubbles: true }));
      });
    };
    const kind = (label: string) => button($('.inbox-kinds'), label);
    await click(kind('Result'));
    expect(ids()).toEqual(['5']);
    expect(kind('Result')!.getAttribute('aria-pressed')).toBe('true');
    await click(kind('Result'));
    expect(ids()).toEqual(['5', '4', '3']);
    await pickAgent('handline.term-build');
    expect(ids()).toEqual(['4']);
    await click(kind('Decision'));
    expect(ids()).toEqual([]);
    expect($('.empty .icon')).not.toBeNull();
    await click(button($('.inbox'), 'Clear filters'));
    expect($$('.inbox-card')).toHaveLength(3);
  });
});

describe('read = gone', () => {
  it('a click on a card marks it read, goes to its task, and the card leaves the list; the daemon agrees', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    s.daemon.knobs.latency(300);
    await click(card(5).querySelector('.inbox-title'));
    expect(location.hash).toBe('#/tasks/12');
    await until(() => s.daemon.inbox.get(5).read, 'the daemon');
    location.hash = '#/inbox'; // back on the page: the card is gone
    await until(() => $('.inbox') && !card(5), 'the card to be gone');
    expect(ids()).toEqual(['4', '3']);
  });

  it('a click on a card of an agent without a task goes to the agent chat and marks it read', async () => {
    const s = setup();
    const added = s.daemon.inbox.add({ kind: 'say', title: 'Hello', agent: 'handline.architect' });
    await mount('#/inbox', s);
    await until(() => $$('.inbox-card').length === 4, 'the cards');
    await click(card(added.id).querySelector('.inbox-title'));
    expect(location.hash).toBe('#/agents/handline.architect');
    await until(() => s.daemon.inbox.get(added.id).read, 'the daemon');
  });

  it('a click on a control in the card does only that control: Reply opens its box and navigates nowhere', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    location.hash = '#/inbox';
    await expand(5);
    await click(button(card(5), 'Reply'));
    expect(card(5).querySelector('textarea')).not.toBeNull();
    expect(location.hash).toBe('#/inbox');
    expect(s.daemon.inbox.get(5).read).toBe(false);
  });

  it('the read mark leads the row at 14 px, filled when unread and hollow when read, and the verbs show on touch', async () => {
    await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    const row = card(3).querySelector('.inbox-row')!;
    const mark = row.firstElementChild!;
    expect(mark.className).toBe('inbox-seen');
    const glyph = mark.querySelector('svg')!;
    expect(glyph.getAttribute('data-icon')).toBe('unread');
    expect(Number(glyph.getAttribute('width'))).toBeGreaterThanOrEqual(14);
    await click(button(card(3), 'Mark read'));
    await until(() => card(3).querySelector('.inbox-seen svg')!.getAttribute('data-icon') === 'read', 'the hollow mark');
    const css = readFileSync(join(import.meta.dirname, '../src/styles/inbox.css'), 'utf8');
    expect(css).toMatch(/@media \(hover: none\) \{\s*\.inbox-hover \{\s*opacity: 1;/);
  });

  it('Enter on the cursor card opens it the same way: marks it read, goes to its task, and the card is gone on return', async () => {
    const s = await mount('#/inbox/5');
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await key('Enter');
    expect(location.hash).toBe('#/tasks/12');
    await until(() => s.daemon.inbox.get(5).read, 'the daemon');
    location.hash = '#/inbox';
    await until(() => $('.inbox') && !card(5), 'the card to be gone');
  });

  it('a question that waits for its answer stays listed after it is opened, and leaves when answered', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await click(card(3).querySelector('.inbox-title'));
    await until(() => s.daemon.inbox.get(3).read, 'the daemon');
    await until(() => s.daemon.inbox.get(5).read, 'the chat read the result'); // opening the architect's chat does that
    location.hash = '#/inbox';
    await until(() => $('.inbox') && card(3), 'the page again');
    expect(card(3).classList.contains('read')).toBe(true);
    expect(ids()).toEqual(['4', '3']); // the architect's chat read its result (5) on opening
    await expand(3);
    await click(button(card(3), 'Yes'));
    await until(() => !card(3), 'the card to leave');
  });

  it('the Mark read icon takes the card off the list without navigating', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    location.hash = '#/inbox';
    await click(button(card(5), 'Mark read'));
    expect(ids()).toEqual(['4', '3']);
    expect(location.hash).toBe('#/inbox');
    await until(() => s.daemon.inbox.get(5).read, 'the daemon');
  });

  it('Mark all read empties the page except the question that waits for its answer; when that is answered the page says nothing waits', async () => {
    await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await click(button($('.inbox-top'), 'Mark all read'));
    await until(() => ids().join() === '3', 'only the question left');
    await expand(3);
    await click(button(card(3), 'Yes'));
    await until(() => !ids().length, 'the page empty');
    expect(text($('.inbox'))).toContain('Nothing waits on you');
  });
});

describe('the verbs', () => {
  it('mark read drops the card, the header count, the bell and the tab title at once, and the daemon agrees', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    s.daemon.knobs.latency(300);
    await click(button(card(5), 'Mark read'));
    await until(() => !card(5), 'the card to leave');
    expect($('.inbox-top h2 [aria-label="2 unread"]')).not.toBeNull();
    expect(document.title).toBe('(2) Handline');
    await until(() => s.daemon.inbox.unread() === 2, 'the daemon');
  });

  it('Mark all read marks every unread item without asking when there are ten or fewer', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await click(button($('.inbox-top'), 'Mark all read'));
    expect($('.inbox-confirm')).toBeNull();
    expect($('.inbox-top h2 [aria-label="0 unread"]')).not.toBeNull();
    await until(() => s.daemon.inbox.unread() === 0, 'the daemon');
    expect(document.title).toBe('Handline');
  });

  it('Mark all read asks first when more than ten are unread', async () => {
    const s = setup();
    for (let n = 0; n < 9; n++) s.daemon.inbox.add({ kind: 'result', title: `Extra ${n}`, agent: 'handline.architect' });
    await mount('#/inbox', s);
    await until(() => $$('.inbox-card').length === 12, 'the cards');
    await click(button($('.inbox-top'), 'Mark all read'));
    expect(text($('.inbox-confirm'))).toContain('Mark all 12 unread');
    expect(s.daemon.inbox.unread()).toBe(12);
    await click(button($('.inbox-confirm'), 'Cancel'));
    expect($('.inbox-confirm')).toBeNull();
    await click(button($('.inbox-top'), 'Mark all read'));
    await click(button($('.inbox-confirm'), 'Mark all read'));
    await until(() => s.daemon.inbox.unread() === 0, 'the daemon');
  });

  it('answering a decision with an option marks it answered and read; the daemon holds it', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await expand(3);
    await click(button(card(3), 'Yes'));
    await until(() => !card(3), 'the card to leave');
    await until(() => s.daemon.inbox.get(3).answered, 'the daemon');
  });

  it('an answer in words goes as text, and the box leaves', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await expand(3);
    await click(button(card(3), 'Answer'));
    const field = card(3).querySelector('textarea')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, 'Relay it, one socket.');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(button(card(3).querySelector('form'), 'Answer'));
    await until(() => !card(3), 'the card to leave');
    await until(() => s.daemon.inbox.get(3).answered, 'the daemon');
  });

  it("a refused answer puts the card back and shows the daemon's reason", async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    s.daemon.knobs.refuse('inbox.answer', 'state', 'Already answered by the founder.');
    await expand(3);
    await click(button(card(3), 'No'));
    await until(() => $('.toast'), 'the toast');
    expect(text($('.toast'))).toContain('Already answered by the founder.');
    expect(card(3).querySelector('[aria-label="Answered"]')).toBeNull();
    expect(button(card(3), 'No')).toBeTruthy();
  });

  it('a reply to a result goes to the daemon and marks it read', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await expand(5);
    await click(button(card(5), 'Reply'));
    const field = card(5).querySelector('textarea')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, 'Thanks, go on.');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(button(card(5), 'Send reply'));
    await until(() => s.daemon.inbox.get(5).read, 'the daemon');
    expect(card(5)).toBeNull();
  });

  it('dismiss takes the card off the list and a refusal brings it back', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await click(button(card(5), 'Dismiss'));
    expect(ids()).toEqual(['4', '3']);
    await until(() => s.daemon.inbox.list({}).items.length === 4, 'the daemon');
    s.daemon.knobs.refuse('chat.dismiss', 'state', 'it is gone');
    await click(button(card(4), 'Dismiss'));
    await until(() => $('.toast'), 'the toast');
    expect($$('.inbox-card')).toHaveLength(2);
  });

  it('a card another client changed follows through the inbox topic', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await act(async () => void s.daemon.inbox.markRead({ ids: [5] }));
    await until(() => !card(5), 'the card to leave');
    await act(async () => void s.daemon.inbox.add({ kind: 'result', title: 'Fresh', agent: 'handline.architect' }));
    await until(() => $$('.inbox-card')[0]!.querySelector('.inbox-title')!.textContent === 'Fresh', 'the new card on top');
  });
});

describe('keys', () => {
  it('j and k move the cursor, m marks the card read, ? opens the key list and Escape closes it', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await key('j');
    expect(card(5).getAttribute('aria-current')).toBe('true');
    await key('j');
    await key('j');
    expect(card(3).getAttribute('aria-current')).toBe('true');
    await key('k');
    await key('m');
    await until(() => s.daemon.inbox.get(4).read, 'the daemon');
    await until(() => !card(4), 'the card to leave');
    expect(ids()).toEqual(['5', '3']);
    await key('?');
    expect($('.inbox-keys')).not.toBeNull();
    await key('Escape');
    expect($('.inbox-keys')).toBeNull();
  });

  it('a key typed in a text field is the text, not a verb', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await expand(5);
    await click(button(card(5), 'Reply'));
    await key('j');
    const field = card(5).querySelector('textarea')!;
    await act(async () => void field.dispatchEvent(new KeyboardEvent('keydown', { key: 'm', bubbles: true })));
    expect(s.daemon.inbox.get(5).read).toBe(false);
  });
});

describe('the a key', () => {
  it('on a decision it opens the answer box and sends nothing', async () => {
    const s = await mount('#/inbox/3');
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await key('a');
    expect(card(3).querySelector('textarea')).not.toBeNull();
    expect(card(3).querySelector('[aria-label="Answered"]')).toBeNull();
    await wait(50);
    expect(s.daemon.inbox.get(3).answered).toBe(false);
  });

  it('on a prompt, which has no answer verb, it does nothing: no dismiss, no mark read', async () => {
    const s = await mount('#/inbox/4');
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await key('a');
    await wait(50);
    expect($$('.inbox-card')).toHaveLength(3);
    expect(card(4).querySelector('textarea')).toBeNull();
    expect(s.daemon.inbox.list({}).items).toHaveLength(5);
  });

  it('on an unread result it opens the reply box', async () => {
    await mount('#/inbox/5');
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    await key('a');
    expect(card(5).querySelector('textarea')).not.toBeNull();
  });
});

describe('deep links and the daemon', () => {
  it('#/inbox/:id puts the cursor on that card', async () => {
    await mount('#/inbox/3');
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    expect(card(3).getAttribute('aria-current')).toBe('true');
  });

  it('#/inbox/:id of an item that is gone says so and links to the inbox', async () => {
    await mount('#/inbox/99');
    await until(() => $$('.inbox-card').length === 3, 'the cards');
    expect(text($('.banner'))).toContain('Item 99 is no longer in the inbox');
  });

  it('a daemon that does not serve the inbox gets "does not serve", no cards and no bell', async () => {
    await mount('#/inbox', setup({ daemon: { inbox: false } }));
    await until(() => text($('.inbox')).includes('does not serve the inbox'), 'the notice');
    expect($$('.inbox-card')).toHaveLength(0);
    expect($('.bell')).toBeNull();
  });

  it('the sidebar bell shows the unread count and opens the inbox', async () => {
    const mem = memory();
    mem.setItem('rail', '1');
    location.hash = '#/agents/handline.architect';
    const s = setup();
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={mem} />);
    });
    await until(() => $('.bell'), 'the bell');
    await until(() => text($('.bell')) === '2', 'the chat read the result'); // opening the architect's chat reads it
    await click($('.bell'));
    expect(location.hash).toBe('#/inbox');
    await until(() => $('.inbox'), 'the page');
  });
});
