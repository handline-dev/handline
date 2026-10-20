import { describe, expect, it } from 'vitest';
import type { setup } from '@handline/fake-daemon/helpers';
import { $, fill, mount, press, text, until, value , verb } from '#test/harness.tsx';

const MINE = 1700; // asked by the founder, a draft
const verbs = (s: ReturnType<typeof setup>) => void (s.daemon.tasks.verbs = true);

async function openTask(id: number) {
  await press($('nav-tasks'));
  await until(() => $(`task-${id}`), 'the task row');
  await press($(`task-${id}`));
  await until(() => $('task-detail'), 'the detail');
}

describe('Ask about a task', () => {
  it('sends nothing for an empty question, then asks and shows the question in the holder chat', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE);
    await press(await verb('ask'));
    await press($('sheet-submit'));
    expect($('sheet-error')?.textContent).toBe('Type the question.');
    await fill('ask-text', 'Where are you on this?');
    await press($('sheet-submit'));
    await until(() => $('sheet') === null, 'the sheet to close');
    const to = m.daemon.tasks.get({ task: MINE }).to;
    const sent = await m.daemon.chat.refs({ agent: to });
    expect(JSON.stringify(sent)).toContain('Question on T1700');
    m.store.stop();
  }, 20000);

  it('keeps the question and says why when the daemon refuses', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE);
    await press(await verb('ask'));
    await fill('ask-text', 'Any news?');
    m.daemon.knobs.refuse('task.ask', 'state', 'that agent is resting');
    await press($('sheet-submit'));
    await until(() => text().includes('that agent is resting'), 'the reason', 3000);
    expect(value('ask-text')).toBe('Any news?');
    m.store.stop();
  }, 20000);
});
