import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, pickTab, press, text, until } from '#test/harness.tsx';

describe('the agent list on the shared nav model', () => {
  it('groups the tree under team names and drops to a flat list with the toggle', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    expect(text()).toContain('architect');
    expect(text()).not.toContain(ARCH);
    await press($('layout-toggle'));
    await until(() => text().includes(ARCH), 'the flat names');
    m.store.stop();
  });

  it('the Needs me chip keeps agents that ask, and All brings the rest back', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    const rows = () => document.body.querySelectorAll('[data-testid^="agent-"]').length;
    const all = rows();
    await press($('chip-needs'));
    await until(() => rows() < all, 'the filter');
    await press($('chip-all'));
    await until(() => rows() === all, 'all again');
    m.store.stop();
  });

  it('a tab opened in the agent shows in its dock, and closing it returns to the chat', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('dock-base'), 'the dock');
    await pickTab('preview');
    await until(() => $('dock-preview'), 'the preview tab');
    await press($('dock-close-preview'));
    await until(() => !$('dock-preview') && $('chat-input'), 'the chat');
    m.store.stop();
  });
});
