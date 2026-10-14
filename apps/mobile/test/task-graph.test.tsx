import { describe, it } from 'vitest';
import { $, mount, press, until } from './harness.tsx';

describe('the task graph', () => {
  it('shows the open tasks as boxes and opens one on a tap', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => $('nav-tasks'), 'the nav');
    await press($('nav-tasks'));
    await until(() => $('graph-toggle'), 'the toggle');
    await press($('graph-toggle'));
    await until(() => $('task-graph'), 'the graph');
    await until(() => $('graph-node-1684'), 'the boxes');
    await press($('graph-node-1684'));
    await until(() => $('task-detail'), 'the detail');
    m.store.stop();
  });
});
