// A hand-off note on the phone's task page: open under its line, and a long one cut to its first lines with a Show more.
import { describe, expect, it } from 'vitest';
import { $, box, mount, press, text, until } from '#test/harness.tsx';

const TASK = 1684;
const notes = (list: string[]) => (s: Parameters<typeof mount>[0] extends ((s: infer S) => void) | undefined ? S : never) => {
  const rows = (s.daemon.tasks as unknown as { rows: { id: number; notes?: { at: string; who: string; text: string }[] }[] }).rows;
  rows.find((r) => r.id === TASK)!.notes = list.map((t, i) => ({ at: `2026-01-0${i + 1}T10:00:00.000Z`, who: 'handline.pc', text: t }));
};
const notesOnScreen = () =>
  [...box.querySelectorAll<HTMLElement>('[data-testid^="event-note-"]')].filter((e) => /^event-note-\d+$/.test(e.dataset.testid!));

async function open(prepare: Parameters<typeof mount>[0]) {
  const m = await mount(prepare);
  await press($('nav-tasks'));
  await until(() => $(`task-${TASK}`), 'the task row');
  await press($(`task-${TASK}`));
  await until(() => $('task-detail'), 'the detail');
  return m;
}

describe('hand-off notes on the task page', () => {
  it('shows a short note open under its line, with no Show more', async () => {
    const m = await open(notes(['Done: the layout is in.']));
    await until(() => notesOnScreen().length > 0, 'the note');
    expect(text(notesOnScreen()[0])).toContain('the layout is in');
    expect(box.querySelector('[data-testid^="event-note-more-"]')).toBeNull();
    m.store.stop();
  }, 20000);

  it('cuts a note longer than eight lines to its first lines until Show more', async () => {
    const long = Array.from({ length: 12 }, (_, i) => `line ${i + 1}`).join('\n');
    const m = await open(notes([long]));
    await until(() => notesOnScreen().length > 0, 'the note');
    expect(text(notesOnScreen()[0])).toContain('line 8');
    expect(text(notesOnScreen()[0])).not.toContain('line 9');
    await press(box.querySelector<HTMLElement>('[data-testid^="event-note-more-"]')!);
    expect(text(notesOnScreen()[0])).toContain('line 12');
    m.store.stop();
  }, 20000);

  it('cuts one long paragraph of eight lines or fewer, so Show more has something to show', async () => {
    const long = Array.from({ length: 140 }, (_, i) => `word${i}`).join(' ');
    const m = await open(notes([long]));
    await until(() => notesOnScreen().length > 0, 'the note');
    expect(text(notesOnScreen()[0])).not.toContain('word139');
    await press(box.querySelector<HTMLElement>('[data-testid^="event-note-more-"]')!);
    expect(text(notesOnScreen()[0])).toContain('word139');
    m.store.stop();
  }, 20000);
});
