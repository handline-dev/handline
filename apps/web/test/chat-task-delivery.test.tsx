// A task handed to an agent is drawn as the board's delivery in the chat, not as a message from the person.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const BODY = 'Founder decisions one at a time';
let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('chat task delivery', () => {
  it('labels it "Task T# handed to <agent>" instead of a from → to line', async () => {
    const s = setup();
    s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: BODY, kind: 'task', tasks: [{ id: 91, title: 'Founder decisions' }] });
    location.hash = `#/agents/${ARCH}`;
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
    const find = () => [...box.querySelectorAll<HTMLElement>('.msg')].find((m) => m.textContent?.includes(BODY));
    for (let t = 0; t < 3000 && !find(); t += 10) await act(async () => void (await new Promise((r) => setTimeout(r, 10))));
    expect(find()?.textContent).toContain(`Task T91 handed to ${ARCH}`);
    expect(find()?.textContent).not.toContain('human');
  });
});
