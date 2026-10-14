import { describe, expect, it } from 'vitest';
import { $, mount, press, text, until } from './harness.tsx';

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

describe('images in a task', () => {
  it('shows the brief without its references and draws its picture', async () => {
    const m = await mount(
      (s) => {
        s.daemon.tasks.verbs = true;
        const up = s.daemon.uploads.put({ name: 'shot.png', mime: 'image/png', bytes: Buffer.from(PNG, 'base64').length, data: PNG });
        s.daemon.tasks.seed({
          id: 3001,
          from: 'founder',
          to: 'handline.auditor',
          title: 'With picture',
          state: 'queued',
          body: `Cut off:\n![the button](attachment:${up.attachment!.id})`,
        });
      },
      undefined,
      'founder',
    );
    await until(() => $('nav-tasks'), 'the nav');
    await press($('nav-tasks'));
    await until(() => $('task-3001'), 'the row');
    await press($('task-3001'));
    await until(() => $('task-detail'), 'the detail');
    await until(() => $('pictures'), 'the picture');
    expect(text($('task-detail'))).toContain('Cut off:');
    expect(text($('task-detail'))).not.toContain('attachment:');
    m.store.stop();
  });
});
