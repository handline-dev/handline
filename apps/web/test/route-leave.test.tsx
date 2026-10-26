// happy-dom keeps no entries behind a hash change, so Back is a spy here; the real entries are checked in a browser (PR evidence).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { leave } from '#src/route.ts';

const depth = () => (history.state as { depth: number }).depth;

afterEach(() => vi.restoreAllMocks());

describe('leave: Esc returns to where the person came from', () => {
  it('counts every move, whoever made it, so a task opened from a chat goes Back to the chat', () => {
    const back = vi.spyOn(history, 'back').mockImplementation(() => {});
    const arrive = (hash: string) => {
      history.replaceState(null, '', hash); // what the browser leaves behind for a new entry: no state yet
      dispatchEvent(new Event('hashchange'));
    };
    arrive('#/agents/ana');
    arrive('#/tasks/7'); // a plain link, as in a chat message
    expect(depth()).toBe(2);
    leave('#/tasks');
    expect(back).toHaveBeenCalledOnce();
    expect(location.hash).toBe('#/tasks/7'); // the browser, not leave, does the moving
  });

  it('with nothing to go back to (the window opened on the page) it replaces the entry with the parent', async () => {
    vi.resetModules();
    history.replaceState(null, '', '#/tasks/9');
    const fresh = await import('#src/route.ts');
    const back = vi.spyOn(history, 'back').mockImplementation(() => {});
    fresh.leave('#/tasks');
    expect(back).not.toHaveBeenCalled();
    expect(location.hash).toBe('#/tasks');
  });
});
