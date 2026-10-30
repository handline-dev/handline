// The photo viewer of the phone, drawn by react-native-web: stepping, marking and the Discard question. A browser gives the stage a size
// and the photo its pixels, so the test hands both over; the touches themselves (pinch, swipe, drag) are checked on a device.
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Image } from 'react-native';
import { $, mount, press, until, pickTab } from '#test/harness.tsx';
import type { setup } from '@handline/fake-daemon/helpers';

// The drawn copy goes up as a JPEG, and the daemon checks the bytes: the stub of the native capture hands over a one-pixel one.
vi.mock('react-native-view-shot', () => ({
  captureRef: async () =>
    'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=',
}));

const AUD = 'handline.auditor';
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

function seed(s: ReturnType<typeof setup>, names: string[]) {
  const ids = names.map((name) => s.daemon.uploads.put({ name, mime: 'image/png', bytes: 70, data: PNG }).attachment!.id);
  s.daemon.chat.send({ agent: AUD, body: 'look', request_id: 'pv', attachments: ids }, 'founder', s.daemon.uploads.message);
  return ids;
}

const $m = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
const mouse = (type: string, x: number, y: number, on: EventTarget = document) =>
  act(
    async () =>
      void on.dispatchEvent(
        new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: type === 'mouseup' ? 0 : 1 }),
      ),
  );
/** One drag on the stage, as a finger would draw a box. */
async function drag() {
  const stage = $m('photo-stage')!;
  await mouse('mousedown', 100, 200, stage);
  await mouse('mousemove', 160, 250, stage);
  await mouse('mousemove', 220, 300, stage);
  await mouse('mouseup', 220, 300, stage);
}
async function marking() {
  const o = await open(['a.png']);
  await until(() => $m('mark-start'), 'the Mark button');
  await press($m('mark-start'));
  await until(() => $m('marks-hint'), 'marking');
  return o;
}

async function open(names: string[]) {
  let ids: string[] = [];
  const m = await mount((s) => void (ids = seed(s, names)));
  await until(() => $(`agent-${AUD}`), 'the rows');
  await press($(`agent-${AUD}`));
  await pickTab('chat');
  await until(() => $(`picture-${ids[0]}`)?.querySelector('div[aria-label]'), 'the thumbnail');
  await press($(`picture-${ids[0]}`));
  await until(() => $m('picture-image'), 'the viewer');
  return { m, ids };
}

describe('the photo viewer', { timeout: 20000 }, () => {
  beforeEach(() => {
    vi.spyOn(Image, 'getSize').mockImplementation(((_u: string, ok: (w: number, h: number) => void) => ok(1000, 500)) as never);
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(private cb: (e: unknown[]) => void) {}
        observe(target: Element) {
          this.cb([{ target }]);
        }
        unobserve() {}
        disconnect() {}
      },
    );
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(400);
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(600);
  });
  afterEach(() => vi.restoreAllMocks());

  it('steps through the photos of a message with the arrows and says which one it shows', async () => {
    const { m, ids } = await open(['a.png', 'b.png']);
    expect($m('picture-count')?.textContent).toBe('1 of 2');
    expect($m('photo-prev')).toBeNull();
    await press($m('photo-next'));
    await until(() => $m('picture-count')?.textContent === '2 of 2', 'the second photo');
    expect($m(`picture-full-${ids[1]}`)).not.toBeNull();
    expect($m('photo-next')).toBeNull();
    await press($m('picture-close'));
    await until(() => !$m('picture-image'), 'the viewer closed');
    m.store.stop();
  });

  it('a single photo has no count and no arrows', async () => {
    const { m } = await open(['only.png']);
    expect($m('picture-count')).toBeNull();
    expect($m('photo-next')).toBeNull();
    m.store.stop();
  });

  it('Mark what to change shows one hint and a Cancel; Cancel with no marks leaves marking without asking', async () => {
    const { m } = await open(['a.png']);
    await until(() => $m('mark-start'), 'the Mark button');
    await press($m('mark-start'));
    await until(() => $m('marks-hint'), 'marking');
    expect($m('marks-hint')?.textContent).toBe('Drag on the photo to mark');
    expect($m('marks-send')).toBeNull();
    expect($m('mark-start')).toBeNull();
    await press($m('marks-cancel'));
    await until(() => $m('mark-start'), 'browsing again');
    expect($m('discard')).toBeNull();
    m.store.stop();
  });

  it('a drag makes a numbered note; with words it becomes Send 1 note to the agent', async () => {
    const { m } = await marking();
    await drag();
    await until(() => $m('notes'), 'the note');
    expect($m('marks-hint')?.textContent).toBe('Write what should change');
    const note = document.querySelector<HTMLTextAreaElement>('[data-testid^="note-"]:not([data-testid^="note-remove"])')!;
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!;
      set.call(note, 'Button is too big');
      note.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await until(() => $m('marks-send'), 'the Send button');
    expect($m('marks-send')?.textContent).toBe(`Send 1 note to ${AUD}`);
    await press($m('marks-send'));
    await until(() => !$m('picture-image'), 'the viewer closed after the send');
    await until(() => document.body.textContent?.includes('Button is too big'), 'the note in the chat');
    expect(document.querySelectorAll('[data-testid="pictures"]').length).toBe(2); // the plain photo, and one message with the drawn copy
    m.store.stop();
  });

  it('Cancel with a mark asks Discard 1 note? and Keep marking keeps it; Discard leaves marking', async () => {
    const { m } = await marking();
    await drag();
    await until(() => $m('notes'), 'the note');
    await press($m('marks-cancel'));
    await until(() => $m('discard'), 'the question');
    expect($m('discard')?.textContent).toContain('Discard 1 note?');
    expect($m('discard')?.textContent).toContain('They are not sent yet.');
    await press($m('discard-keep'));
    expect($m('discard')).toBeNull();
    expect($m('notes')).not.toBeNull();
    await press($m('picture-close')); // leaving the viewer asks the same
    await until(() => $m('discard'), 'the question again');
    await press($m('discard-yes'));
    await until(() => !$m('picture-image'), 'the viewer closed');
    m.store.stop();
  });
});
