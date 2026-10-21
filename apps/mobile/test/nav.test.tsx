import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, text, until } from '#test/harness.tsx';

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

  it('opening an agent puts it in the dock, and closing its tab leaves the page', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $(`dock-${ARCH}`), 'the dock tab');
    await press($(`dock-close-${ARCH}`));
    await until(() => $(`agent-${ARCH}`), 'the list');
    m.store.stop();
  });
});
