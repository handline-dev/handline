// Images in task bodies: `![caption](attachment:ID)` draws the picture; the body editor takes a pasted image, uploads it, and keeps the text as typed.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from '@tiptap/markdown';
import Image from '@tiptap/extension-image';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';
import { forEditor, keepTyped } from '../src/bodyMarkdown.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
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
const $ = (sel: string, from: ParentNode = box) => from.querySelector<HTMLElement>(sel);
const $$ = (sel: string, from: ParentNode = box) => [...from.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const button = (label: string, within: ParentNode = box) => $$('button', within).find((b) => (b.textContent ?? '').trim().startsWith(label));

const PNG = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), (c) =>
  c.charCodeAt(0),
);

async function mount(s = setup()) {
  const store = new Store(s.client);
  location.hash = '#/tasks/1700';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
  await until(() => $('.tverbs'), 'the task');
  return s;
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('the body keeps its text', () => {
  const same = (md: string) => {
    const e = new Editor({ extensions: [StarterKit, Markdown], content: forEditor(md), contentType: 'markdown' });
    keepTyped(e);
    return e.getMarkdown();
  };
  it('paths, snake_case, HTML-looking text and ampersands come back as written', () => {
    for (const md of ['/tmp/a_b_c.ts and snake_case_name', 'a <b>x</b> and <script>1</script>', '1 < 2 & 3 &amp; 4', '- one\n- two', '**bold** and `a_b`'])
      expect(same(md)).toBe(md);
  });
});

describe('an image after a single newline', () => {
  it('survives a rich edit', () => {
    const md = 'text\n![a](attachment:x)\nmore';
    const e = new Editor({ extensions: [StarterKit, Image, Markdown], content: forEditor(md), contentType: 'markdown' });
    keepTyped(e);
    e.commands.insertContentAt(1, 'Z');
    expect(e.getMarkdown()).toContain('![a](attachment:x)');
    expect(e.getMarkdown()).toContain('Ztext');
  });
});

describe('images in a task body', () => {
  it('a reference in the body draws the picture, and a click opens the original', async () => {
    URL.createObjectURL = () => 'blob:test/1';
    const s = setup();
    const id = s.daemon.uploads.put({ name: 'dot.png', mime: 'image/png', bytes: PNG.length, data: btoa(String.fromCharCode(...PNG)) }).attachment!.id;
    s.daemon.tasks.edit({ task: 1700, body: `See below\n![dot](attachment:${id})` });
    await mount(s);
    await until(() => $('.tbody-text .attachment img'), 'the picture in the body');
    expect($('.tbody-text')!.textContent).toContain('See below');
    expect($('.tbody-text')!.textContent).not.toContain('attachment:');
    await click($('.tbody-text .attachment'));
    await until(() => $$('.ui-dialog img').length, 'the original');
  });

  it('a pasted image in the editor is uploaded and saved in the body as a reference', async () => {
    const s = setup();
    const old = s.daemon.uploads.put({ name: 'old.png', mime: 'image/png', bytes: PNG.length, data: btoa(String.fromCharCode(...PNG)) }).attachment!.id;
    s.daemon.tasks.edit({ task: 1700, body: `![old](attachment:${old})` });
    await mount(s);
    await click(button('Edit')!);
    await until(() => $('.ProseMirror'), 'the editor');
    const file = new File([PNG], 'shot.png', { type: 'image/png' });
    await act(async () => {
      const e = new Event('paste', { bubbles: true, cancelable: true });
      Object.defineProperty(e, 'clipboardData', { value: { files: [file], getData: () => '', types: ['Files'] } });
      $('.ProseMirror')!.dispatchEvent(e);
    });
    await until(() => $$('.ProseMirror .attachment').length === 2, 'the pasted image beside the old one');
    await click(button('Save', $('.tsheet')!)!);
    await until(() => /!\[shot\]\(attachment:[^)]+\)/.test(s.daemon.tasks.get({ task: 1700 }).body), 'the saved body');
    const body = s.daemon.tasks.get({ task: 1700 }).body;
    expect(body).toContain(`![old](attachment:${old})`);
  });

  it('in the source view a pasted image adds its line to the text', async () => {
    const s = setup();
    await mount(s);
    await click(button('Edit')!);
    await until(() => $('.ProseMirror'), 'the editor');
    await click(button('Source')!);
    const file = new File([PNG], 'shot.png', { type: 'image/png' });
    await act(async () => {
      const e = new Event('paste', { bubbles: true, cancelable: true });
      Object.defineProperty(e, 'clipboardData', { value: { files: [file] } });
      $('.tsheet textarea')!.dispatchEvent(e);
    });
    await until(() => /!\[shot\]\(attachment:/.test(($('.tsheet textarea') as HTMLTextAreaElement).value), 'the line in the source');
    await click(button('Save', $('.tsheet')!)!);
    await until(() => /!\[shot\]\(attachment:[^)]+\)/.test(s.daemon.tasks.get({ task: 1700 }).body), 'the saved body');
  });

  it('after a source-view paste, the rich view shows the picture and keeps the line', async () => {
    const s = setup();
    await mount(s);
    await click(button('Edit')!);
    await until(() => $('.ProseMirror'), 'the editor');
    await click(button('Source')!);
    const file = new File([PNG], 'shot.png', { type: 'image/png' });
    await act(async () => {
      const e = new Event('paste', { bubbles: true, cancelable: true });
      Object.defineProperty(e, 'clipboardData', { value: { files: [file] } });
      $('.tsheet textarea')!.dispatchEvent(e);
    });
    await until(() => /!\[shot\]\(attachment:/.test(($('.tsheet textarea') as HTMLTextAreaElement).value), 'the line in the source');
    await click(button('Rich view')!);
    await until(() => $$('.ProseMirror .attachment').length === 1, 'the picture in the rich view');
    await click(button('Save', $('.tsheet')!)!);
    await until(() => /!\[shot\]\(attachment:[^)]+\)/.test(s.daemon.tasks.get({ task: 1700 }).body), 'the saved body');
  });

  it('a Save in the same tick as a paste still waits for the upload', async () => {
    const s = setup();
    await mount(s);
    await click(button('Edit')!);
    await until(() => $('.ProseMirror'), 'the editor');
    const file = new File([PNG], 'shot.png', { type: 'image/png' });
    await act(async () => {
      const e = new Event('paste', { bubbles: true, cancelable: true });
      Object.defineProperty(e, 'clipboardData', { value: { files: [file], getData: () => '', types: ['Files'] } });
      $('.ProseMirror')!.dispatchEvent(e);
      button('Save', $('.tsheet')!)!.click();
    });
    await until(() => $$('.ProseMirror .attachment').length === 1, 'the pasted image');
    expect(/shot/.test(s.daemon.tasks.get({ task: 1700 }).body)).toBe(false);
    await click(button('Save', $('.tsheet')!)!);
    await until(() => /!\[shot\]\(attachment:[^)]+\)/.test(s.daemon.tasks.get({ task: 1700 }).body), 'the saved body');
  });
});
