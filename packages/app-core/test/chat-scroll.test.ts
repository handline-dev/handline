import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ChatMessage, TranscriptItem } from '@handline/client';
import { chatScroll, chatView, coverage, START, type Geometry } from '@handline/app-core';

const g = (o: Partial<Geometry> = {}): Geometry => ({ lines: 1000, view: 300, sent: 900, ...o });
const msg = (id: number, at: string, from = 'a'): ChatMessage => ({
  id,
  at,
  state_at: at,
  from,
  to: 'b',
  state: 'delivered',
  landing: 'delivered',
  sending_reason: null,
  body: 'x',
  delivered_at: null,
  typed_at: null,
  landed_at: null,
  parent: null,
  kind: 'message',
  tasks: [],
});
const item = (id: number, at: string): TranscriptItem => ({ id, at, kind: 'reply', text: 'y' });

test('entering on the PC lands at the bottom, with no room under the lines', () => {
  const a = chatScroll(START, { type: 'enter' }, g(), 'bottom');
  assert.equal(a.to, 700);
  assert.equal(a.room, 0);
  assert.equal(a.state.mode, 'follow');
});

test('entering on the phone holds the last sent message at the top when the tail fits under it', () => {
  const a = chatScroll(START, { type: 'enter' }, g({ lines: 1000, sent: 900 }), 'lastSent');
  assert.equal(a.to, 900);
  assert.equal(a.room, 200);
  assert.equal(a.state.mode, 'hold');
});

test('entering on the phone lands at the bottom when the tail does not fit under the sent message', () => {
  const a = chatScroll(START, { type: 'enter' }, g({ sent: 400 }), 'lastSent');
  assert.equal(a.to, 700);
  assert.equal(a.state.mode, 'follow');
});

test('a late picture of a known size moves nothing; one that grows a followed chat keeps it at the bottom', () => {
  const free = chatScroll({ mode: 'free', newer: 0 }, { type: 'grew' }, g(), 'bottom');
  assert.equal(free.to, null);
  const follow = chatScroll(START, { type: 'grew' }, g({ lines: 1200 }), 'bottom');
  assert.equal(follow.to, 900);
});

test('sending goes to the bottom on the PC and to the top on the phone, gliding', () => {
  const pc = chatScroll({ mode: 'free', newer: 3 }, { type: 'sent' }, g(), 'bottom');
  assert.deepEqual([pc.to, pc.smooth, pc.state], [700, true, START]);
  const phone = chatScroll({ mode: 'free', newer: 3 }, { type: 'sent' }, g(), 'lastSent');
  assert.deepEqual([phone.to, phone.smooth, phone.state.mode], [900, true, 'hold']);
});

test('a reply follows when the reader is at the bottom; above it only the count grows', () => {
  assert.equal(chatScroll(START, { type: 'grew' }, g({ lines: 1100 }), 'bottom').to, 800);
  const read = chatScroll({ mode: 'free', newer: 1 }, { type: 'added', count: 2 }, g(), 'bottom');
  assert.equal(read.to, null);
  assert.equal(read.state.newer, 3);
});

test('a held message stays put while its reply grows; the room shrinks', () => {
  const held = { mode: 'hold', newer: 0 } as const;
  const a = chatScroll(held, { type: 'grew' }, g({ lines: 1100, sent: 900 }), 'lastSent');
  assert.deepEqual([a.to, a.room], [900, 100]);
});

test('after the person scrolls nothing moves until they are back at the bottom', () => {
  const up = chatScroll(START, { type: 'scrolled', atBottom: false }, g(), 'bottom');
  assert.equal(up.state.mode, 'free');
  assert.equal(chatScroll(up.state, { type: 'grew' }, g({ lines: 1500 }), 'bottom').to, null);
  assert.equal(chatScroll(up.state, { type: 'scrolled', atBottom: true }, g(), 'bottom').state.mode, 'follow');
});

test('chatView draws only what both sources have read, and no head of system lines alone', () => {
  // messages reach back to day 1, the transcript only to day 2 with more behind it
  const messages = [msg(1, '2026-01-01T10:00:00Z'), msg(2, '2026-01-02T10:00:00Z'), msg(3, '2026-01-03T10:00:00Z')];
  const items = [item(10, '2026-01-02T12:00:00Z'), item(11, '2026-01-03T12:00:00Z')];
  const s = { messages, items, moreMessages: false, moreItems: true };
  assert.equal(coverage(s), '2026-01-02T12:00:00Z');
  const v = chatView(s, 'mine', 'b', 'human');
  assert.deepEqual(
    v.entries.map((e) => e.id),
    [10, 3, 11],
  );
  assert.ok(v.entries.every((e) => e.at >= '2026-01-02T12:00:00Z'));
  assert.equal(v.older, true);
});

test('chatView draws everything when both sources reached the start', () => {
  const v = chatView(
    { messages: [msg(1, '2026-01-01T10:00:00Z')], items: [item(2, '2026-01-02T10:00:00Z')], moreMessages: false, moreItems: false },
    'mine',
    'b',
    'human',
  );
  assert.equal(v.entries.length, 2);
  assert.equal(v.floor, null);
});

test('on the phone the room under the lines follows the geometry in every mode, so the last send can always reach the top', () => {
  for (const state of [START, { mode: 'hold' as const, newer: 0 }, { mode: 'free' as const, newer: 0 }]) {
    for (const event of [{ type: 'grew' }, { type: 'scrolled', atBottom: true }, { type: 'scrolled', atBottom: false }] as const) {
      assert.equal(chatScroll(state, event, g({ lines: 1000, sent: 900 }), 'lastSent').room, 200, `${state.mode} ${event.type}`);
    }
  }
  assert.equal(chatScroll(START, { type: 'grew' }, g({ lines: 1300, sent: 900 }), 'lastSent').room, 0);
});

test('on the PC there is never any room', () => {
  assert.equal(chatScroll({ mode: 'free', newer: 0 }, { type: 'grew' }, g(), 'bottom').room, 0);
});

test("on the phone, back at the end of the lines with the last send's tail fitting is the held place, so a resize keeps the send at the top", () => {
  const free = { mode: 'free' as const, newer: 0 };
  const back = chatScroll(free, { type: 'scrolled', atBottom: true }, g({ lines: 1000, view: 300, sent: 900 }), 'lastSent');
  assert.equal(back.state.mode, 'hold');
  assert.equal(chatScroll(back.state, { type: 'grew' }, g({ lines: 1000, view: 200, sent: 900 }), 'lastSent').to, 900); // the keyboard came up
  // A tail that does not fit under the send is followed as before, and the PC always follows.
  assert.equal(
    chatScroll(free, { type: 'scrolled', atBottom: true }, g({ lines: 1500, view: 300, sent: 900 }), 'lastSent').state.mode,
    'follow',
  );
  assert.equal(chatScroll(free, { type: 'scrolled', atBottom: true }, g({ sent: 900 }), 'bottom').state.mode, 'follow');
});
