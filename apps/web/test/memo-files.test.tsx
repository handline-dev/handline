// A task memo's files in the task view: an image inline (a click opens it full size) and a Markdown file rendered, not shown as raw text.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('memo files', () => {
  it('shows an image inline and renders a Markdown file', async () => {
    const s = setup();
    s.daemon.tasks.verbs = true;
    const img = s.daemon.uploads.addMemoFile('mock.png', 'image/png', PNG);
    const md = s.daemon.uploads.addMemoFile(
      'proposal.md',
      'text/markdown',
      Buffer.from('# The plan\n\nDo **two** things:\n\n- first\n- second\n'),
    );
    s.daemon.tasks.memo({ task: 1684, text: 'The proposal', files: [img.id, md.id] }, 'add', 'founder');
    location.hash = '#/tasks/1684';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
    });
    await until(() => box.querySelector('button[aria-label="Open mock.png"] img'), 'the image');
    await until(() => box.querySelector('section[aria-label="proposal.md"] h1'), 'the rendered heading');
    const section = box.querySelector('section[aria-label="proposal.md"]')!;
    expect(section.querySelector('h1')?.textContent).toBe('The plan');
    expect(section.querySelector('strong')?.textContent).toBe('two');
    expect(section.querySelectorAll('li')).toHaveLength(2);
    expect(section.textContent).not.toContain('**');
    await act(async () => void box.querySelector<HTMLElement>('button[aria-label="Open mock.png"]')!.click());
    await until(() => document.querySelector('[role="dialog"] img'), 'the full-size image');
  });

  it("draws a relative picture of a Markdown file from the memo's image of the same name, and keeps the alt text for one it lacks", async () => {
    const s = setup();
    s.daemon.tasks.verbs = true;
    const img = s.daemon.uploads.addMemoFile('cmp-sidebar.png', 'image/png', PNG);
    const md = s.daemon.uploads.addMemoFile(
      'plan.md',
      'text/markdown',
      Buffer.from('Look:\n\n![Sidebar](docs/img/cmp-sidebar.png)\n\n![Missing shot](docs/img/none.png)\n'),
    );
    s.daemon.tasks.memo({ task: 1684, text: 'Plan', files: [img.id, md.id] }, 'add', 'founder');
    location.hash = '#/tasks/1684';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
    });
    await until(
      () => box.querySelector('section[aria-label="plan.md"] button[aria-label="Open Sidebar"] img'),
      'the picture inside the file',
    );
    const section = box.querySelector('section[aria-label="plan.md"]')!;
    expect(section.querySelectorAll('img')).toHaveLength(1);
    expect(section.textContent).toContain('Missing shot');
  });
});
