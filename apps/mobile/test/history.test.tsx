import { describe, expect, it } from 'vitest';
import { ARCH, until, $, press, mount } from './harness.tsx';
import { History } from '../src/history.tsx';

describe('the history', () => {
  it('walks back and forward, and a new move drops what lay ahead', () => {
    const h = new History();
    h.push('tab', 'inbox');
    h.push('tab', 'tasks');
    expect(h.back()).toBe(true);
    expect(h.get('tab', 'agents')).toBe('inbox');
    h.push('tab', 'usage');
    expect(h.forward()).toBe(false);
    expect(h.back() && h.back()).toBe(true);
    expect(h.get('tab', 'agents')).toBe('agents');
    expect(h.back()).toBe(false);
  });
});

describe('back across the moves in the app', () => {
  it('the Back button on the agent page is a step back in the history, to the list', async () => {
    const m = await mount();
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($('nav-tasks'));
    await press($('nav-agents'));
    await press($(`agent-${ARCH}`));
    await until(() => $('back'), 'the agent page');
    await press($('back'));
    await until(() => $(`agent-${ARCH}`), 'the list');
    expect($('nav-tasks')).not.toBeNull();
    m.store.stop();
  });
});
