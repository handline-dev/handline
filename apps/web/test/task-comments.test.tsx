// Comments on a task: the composer under the list; empty is not sent, a sent one joins the list and clears the box, a refusal keeps the text and says why.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV => {
  const data = new Map<string, string>();
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const section = () => box.querySelector<HTMLElement>('.tbody');
const send = () => [...section()!.querySelectorAll('button')].find((b) => /^Comment(?!s)/.test(b.textContent ?? '')) as HTMLButtonElement;
const typeInto = async (value: string) => {
  const el = section()!.querySelector('.ProseMirror')!;
  await act(async () => void (el.textContent = value));
  await new Promise((r) => setTimeout(r, 50));
};

async function mount(seed?: (s: ReturnType<typeof setup>) => void) {
  const s = setup();
  s.daemon.tasks.verbs = true;
  seed?.(s);
  location.hash = '#/tasks/1688';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(section, 'the comments');
  await until(() => send(), 'the composer');
  return s;
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('a comment', () => {
  it('shows its Markdown', async () => {
    await mount((s) => s.daemon.tasks.memo({ task: 1688, text: '**why** this:\n- `a`\n- b' }, 'add', 'human'));
    await until(() => section()!.querySelector('.comment strong'), 'the comment');
    expect([...section()!.querySelectorAll('.comment strong')].map((b) => b.textContent)).toContain('why');
    expect([...section()!.querySelectorAll('.comment ul li')].map((l) => l.textContent)).toEqual(['a', 'b']);
  });
});

describe('a comment with a shell block', () => {
  it('shows a command card with Copy and no Run on a page with no panes', async () => {
    await mount((s) => s.daemon.tasks.memo({ task: 1688, text: 'Try:\n```sh\npnpm test\n```' }, 'add', 'human'));
    await until(() => section()!.querySelector('.command-card'), 'the card');
    const card = section()!.querySelector('.command-card')!;
    expect(card.querySelector('code')?.textContent).toBe('pnpm test');
    expect(card.querySelector('button[title="Copy"], button[aria-label="Copy"]')).not.toBeNull();
    expect([...card.querySelectorAll('button')].some((b) => (b.title || b.getAttribute('aria-label') || '').startsWith('Run'))).toBe(false);
  });
});

describe('a reply to a comment', () => {
  it('names the comment it answers, sends reply, and shows what it answered', async () => {
    const s = await mount((x) => x.daemon.tasks.memo({ task: 1688, text: 'Which port is it?' }, 'add', 'handline.architect'));
    await until(() => section()!.querySelector('.comment'), 'the comment');
    await act(
      async () => void ([...section()!.querySelectorAll('.comment button')].find((b) => b.textContent === 'Reply') as HTMLElement).click(),
    );
    expect(section()!.querySelector('[data-replying]')?.textContent).toContain('Replying to handline.architect: Which port is it?');
    await typeInto('7443');
    await act(async () => void send().click());
    await until(() => section()!.querySelector('[data-reply-to]'), 'the answer with what it answers');
    expect(section()!.querySelector('[data-reply-to]')!.textContent).toContain('↩ handline.architect: Which port is it?');
    expect(s.daemon.tasks.find(1688).memos!.at(-1)).toMatchObject({ text: '7443', reply_to: 1 });
    expect(section()!.querySelector('[data-replying]')).toBeNull(); // the next comment is a plain one
  });
});

describe('Comments on a task', () => {
  it('does not send an empty comment', async () => {
    await mount();
    expect(send().disabled).toBe(true);
    await typeInto('   ');
    expect(send().disabled).toBe(true);
  });

  it('sends the comment, lists it with its author and clears the box', async () => {
    const s = await mount();
    await typeInto('why this way? @handline.architect');
    await act(async () => void send().click());
    await until(
      () => s.daemon.tasks.get({ task: 1688 }).memos.some((m: { text: string }) => m.text.includes('why this way?')),
      'the daemon has it',
    );
    await until(() => section()!.querySelector('.comment')?.textContent?.includes('why this way?'), 'the comment in the list');
    await until(() => !section()!.querySelector('textarea'), 'the box to clear');
    expect(box.querySelector('[aria-label="Message about this task"]')).toBeNull(); // no separate reply box
  });

  it('offers a picture to mark, and no window capture, in the task detail', async () => {
    await mount();
    const labels = [...box.querySelectorAll('button')].map((b) => b.getAttribute('aria-label') ?? b.textContent);
    expect(labels).toContain('Attach a picture');
    expect(labels).not.toContain('Capture a window');
  });

  it('keeps the text and says why when the daemon refuses', async () => {
    const s = await mount();
    s.daemon.knobs.refuse('task.comment.add', 'state', 'the task is closed', undefined, 1);
    await typeInto('still there?');
    await act(async () => void send().click());
    await until(() => section()!.textContent!.includes('the task is closed'), 'the refusal');
    expect(section()!.querySelector('.ProseMirror')!.textContent).toBe('still there?');
  });

  it('Escape in an inline field leaves it as it was and saves nothing', async () => {
    const s = await mount();
    const before = s.daemon.tasks.get({ task: 1688 }).title;
    await act(async () => void box.querySelector<HTMLElement>('[aria-label="Edit Title"]')!.click());
    const field = box.querySelector<HTMLInputElement>('input[aria-label="Title"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, 'Never saved');
      field.dispatchEvent(new Event('input', { bubbles: true }));
      field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(box.querySelector('input[aria-label="Title"]')).toBeNull();
    await wait(50);
    expect(s.daemon.tasks.get({ task: 1688 }).title).toBe(before);
  });

  it('draws a picture inside a comment as a small thumbnail, never the tall chat copy', async () => {
    URL.createObjectURL = () => 'blob:test/1';
    const png = Uint8Array.from(
      atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='),
      (c) => c.charCodeAt(0),
    );
    await mount((s) => {
      const id = s.daemon.uploads.put({ name: 'dot.png', mime: 'image/png', bytes: png.length, data: btoa(String.fromCharCode(...png)) })
        .attachment!.id;
      s.daemon.tasks.memo({ task: 1688, text: `![a](attachment:${id})\n![b](attachment:${id})` }, 'add', 'web-test');
    });
    await until(() => section()!.querySelector('.comment .attachment'), 'the picture');
    const pic = section()!.querySelector('.comment .attachment')!;
    expect(pic.className).toContain('w-20');
    expect(pic.className).not.toContain('h-40');
    // blank space between pictures would break their row: the two sit side by side with a gap and nothing between them
    const [a, b] = section()!.querySelectorAll('.comment .attachment');
    expect(a!.nextSibling).toBe(b);
    expect(a!.className).toContain('mr-1.5');
  });
});
