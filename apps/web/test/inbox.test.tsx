// U10: the Inbox page on the fake daemon's inbox: skeleton, the cards of each kind, filters, the bell, the verbs and their refusals, the keys,
// deep links, and a daemon that does not serve the inbox.
import { act } from 'react';
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
  [...(within ?? box).querySelectorAll('button, a')].find((b) => text(b).trim() === label) as HTMLElement | undefined;
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

// Seeded by the fake: 1 result (read, task 7), 2 block (read), 3 decision, 4 prompt, 5 result (task 12); 3, 4 and 5 are unread.
describe('the page', () => {
  it('shows the skeleton while loading, then the cards newest first with the unread count', async () => {
    const s = setup();
    s.daemon.knobs.latency(400);
    await mount('#/inbox', s);
    await until(() => $('.inbox .skeleton'), 'the skeleton');
    expect($('.inbox .skeleton')!.getAttribute('aria-busy')).toBe('true');
    await until(() => $$('.inbox-card').length === 5, 'the cards', 600);
    expect($('.inbox .skeleton')).toBeNull();
    expect($$('.inbox-card').map((c) => c.dataset.item)).toEqual(['5', '4', '3', '2', '1']);
    expect(text($('.inbox-top h2'))).toContain('3 unread');
    expect(document.title).toBe('(3) Handline');
    expect(card(3).querySelector('.inbox-dot')!.textContent).toBe('●');
    expect(card(1).querySelector('.inbox-dot')!.textContent).toBe('○');
  });

  it('each kind shows its own verbs: options and Answer on a decision, Reply on a result, only Open on a block', async () => {
    await mount();
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    expect([...card(3).querySelectorAll('button')].map(text)).toEqual(['Yes', 'No', 'Answer…', 'Mark read', 'Dismiss']);
    expect(button(card(5), 'Reply…')).toBeTruthy();
    expect(button(card(2), 'Reply…')).toBeUndefined();
    expect(card(4).querySelector('[data-prompt-slot]')!.textContent).toBe('rm -rf build');
    expect(button(card(4), 'Reply…')).toBeUndefined();
    expect(button(card(1), 'Open task')!.getAttribute('href')).toBe('#/tasks/7');
    expect(button(card(2), 'Open chat')!.getAttribute('href')).toBe('#/agents/handline.architect');
  });

  it('evidence shows its first three lines and Read whole shows the rest', async () => {
    await mount();
    await until(() => card(1), 'the cards');
    expect(text(card(1).querySelector('pre'))).not.toContain('nothing else touched');
    await click(button(card(1), 'Read whole'));
    expect(text(card(1).querySelector('pre'))).toContain('nothing else touched');
  });

  it('filters by kind and by agent, says when nothing matches, and Clear filters brings the list back', async () => {
    await mount();
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    const pick = async (label: string, value: string) => {
      const sel = $(`select[aria-label="${label}"]`) as HTMLSelectElement;
      await act(async () => {
        const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!;
        set.call(sel, value);
        sel.dispatchEvent(new Event('change', { bubbles: true }));
      });
    };
    await pick('Kind', 'result');
    expect($$('.inbox-card').map((c) => c.dataset.item)).toEqual(['5', '1']);
    await pick('Agent', 'handline.term-build');
    expect($$('.inbox-card').map((c) => c.dataset.item)).toEqual(['1']);
    await pick('Kind', 'decision');
    expect(text($('.inbox'))).toContain('No items match');
    await click(button($('.inbox'), 'Clear filters'));
    expect($$('.inbox-card')).toHaveLength(5);
  });
});

describe('the verbs', () => {
  it('mark read drops the card dot, the header count, the bell and the tab title at once, and the daemon agrees', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    s.daemon.knobs.latency(300);
    await click(button(card(5), 'Mark read'));
    expect(card(5).querySelector('.inbox-dot')!.textContent).toBe('○');
    expect(text($('.inbox-top h2'))).toContain('2 unread');
    expect(document.title).toBe('(2) Handline');
    await until(() => s.daemon.inbox.unread() === 2, 'the daemon');
  });

  it('Mark all read marks every unread item without asking when there are ten or fewer', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    await click(button($('.inbox-top'), 'Mark all read'));
    expect($('.inbox-confirm')).toBeNull();
    expect(text($('.inbox-top h2'))).toContain('0 unread');
    await until(() => s.daemon.inbox.unread() === 0, 'the daemon');
    expect(document.title).toBe('Handline');
  });

  it('Mark all read asks first when more than ten are unread', async () => {
    const s = setup();
    for (let n = 0; n < 9; n++) s.daemon.inbox.add({ kind: 'result', title: `Extra ${n}`, agent: 'handline.architect' });
    await mount('#/inbox', s);
    await until(() => $$('.inbox-card').length === 14, 'the cards');
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
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    await click(button(card(3), 'Yes'));
    expect(text(card(3))).toContain('Answered');
    await until(() => s.daemon.inbox.get(3).answered, 'the daemon');
    expect(button(card(3), 'Yes')).toBeUndefined();
  });

  it('an answer in words goes as text, and the box leaves', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    await click(button(card(3), 'Answer…'));
    const field = card(3).querySelector('textarea')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, 'Relay it, one socket.');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(button(card(3), 'Answer'));
    expect(card(3).querySelector('textarea')).toBeNull();
    await until(() => s.daemon.inbox.get(3).answered, 'the daemon');
  });

  it("a refused answer puts the card back and shows the daemon's reason", async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    s.daemon.knobs.refuse('inbox.answer', 'state', 'Already answered by the founder.');
    await click(button(card(3), 'No'));
    await until(() => $('.toast'), 'the toast');
    expect(text($('.toast'))).toContain('Already answered by the founder.');
    expect(text(card(3))).not.toContain('Answered');
    expect(button(card(3), 'No')).toBeTruthy();
  });

  it('a reply to a result goes to the daemon and marks it read', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    await click(button(card(5), 'Reply…'));
    const field = card(5).querySelector('textarea')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, 'Thanks, go on.');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(button(card(5), 'Send reply'));
    await until(() => s.daemon.inbox.get(5).read, 'the daemon');
    expect(card(5).querySelector('.inbox-dot')!.textContent).toBe('○');
  });

  it('dismiss takes the card off the list and a refusal brings it back', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    await click(button(card(5), 'Dismiss'));
    expect($$('.inbox-card').map((c) => c.dataset.item)).toEqual(['4', '3', '2', '1']);
    await until(() => s.daemon.inbox.list({}).items.length === 4, 'the daemon');
    s.daemon.knobs.refuse('chat.dismiss', 'state', 'it is gone');
    await click(button(card(4), 'Dismiss'));
    await until(() => $('.toast'), 'the toast');
    expect($$('.inbox-card')).toHaveLength(4);
  });

  it('a card another client changed follows through the inbox topic', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    await act(async () => void s.daemon.inbox.markRead({ ids: [3] }));
    await until(() => card(3).querySelector('.inbox-dot')!.textContent === '○', 'the read mark');
    await act(async () => void s.daemon.inbox.add({ kind: 'result', title: 'Fresh', agent: 'handline.architect' }));
    await until(() => $$('.inbox-card')[0]!.querySelector('.inbox-title')!.textContent === 'Fresh', 'the new card on top');
  });
});

describe('keys', () => {
  it('j and k move the cursor, m marks the card read, ? opens the key list and Escape closes it', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    await key('j');
    expect(card(5).getAttribute('aria-current')).toBe('true');
    await key('j');
    await key('j');
    expect(card(3).getAttribute('aria-current')).toBe('true');
    await key('m');
    await until(() => s.daemon.inbox.get(3).read, 'the daemon');
    await key('k');
    expect(card(4).getAttribute('aria-current')).toBe('true');
    await key('?');
    expect($('.inbox-keys')).not.toBeNull();
    await key('Escape');
    expect($('.inbox-keys')).toBeNull();
  });

  it('a key typed in a text field is the text, not a verb', async () => {
    const s = await mount();
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    await click(button(card(5), 'Reply…'));
    await key('j');
    const field = card(5).querySelector('textarea')!;
    await act(async () => void field.dispatchEvent(new KeyboardEvent('keydown', { key: 'm', bubbles: true })));
    expect(s.daemon.inbox.get(5).read).toBe(false);
  });
});

describe('the a key', () => {
  it('on a decision it opens the answer box and sends nothing', async () => {
    const s = await mount('#/inbox/3');
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    await key('a');
    expect(card(3).querySelector('textarea')).not.toBeNull();
    expect(text(card(3))).not.toContain('Answered');
    await wait(50);
    expect(s.daemon.inbox.get(3).answered).toBe(false);
  });

  it('on a block, which has no answer verb, it does nothing: no dismiss, no mark read', async () => {
    const s = await mount('#/inbox/2');
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    await key('a');
    await wait(50);
    expect($$('.inbox-card')).toHaveLength(5);
    expect(card(2).querySelector('textarea')).toBeNull();
    expect(s.daemon.inbox.list({}).items).toHaveLength(5);
  });

  it('on an unread result it opens the reply box', async () => {
    await mount('#/inbox/5');
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    await key('a');
    expect(card(5).querySelector('textarea')).not.toBeNull();
  });
});

describe('deep links and the daemon', () => {
  it('#/inbox/:id puts the cursor on that card', async () => {
    await mount('#/inbox/3');
    await until(() => $$('.inbox-card').length === 5, 'the cards');
    expect(card(3).getAttribute('aria-current')).toBe('true');
  });

  it('#/inbox/:id of an item that is gone says so and links to the inbox', async () => {
    await mount('#/inbox/99');
    await until(() => $$('.inbox-card').length === 5, 'the cards');
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
    expect(text($('.bell'))).toBe('3');
    await click($('.bell'));
    expect(location.hash).toBe('#/inbox');
    await until(() => $('.inbox'), 'the page');
  });
});
