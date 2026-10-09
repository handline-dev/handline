import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AgentView } from '@handline/client';
import { CONFIRM_VERBS, askOf, VERBS, callOf, verbsOf } from '../src/index.ts';

const row = (state: string) => ({ name: 'a.b', state }) as AgentView;
const ids = (state: string, session_open: boolean, session_id: string | null) =>
  verbsOf(row(state), { session_open, session_id }).map((v) => v.id);

test('an open session offers restart, restart fresh, close, park, fold and move', () => {
  assert.deepEqual(ids('running', true, 's1'), ['restart', 'restart_fresh', 'close', 'park', 'fold', 'move']);
});
test('a parked agent with a session to resume offers wake and move; one never started only move', () => {
  assert.deepEqual(ids('parked', false, 's1'), ['wake', 'move']);
  assert.deepEqual(ids('parked', false, null), ['move']);
});
test('a stalled row offers clear stall in place of wake', () => {
  assert.deepEqual(ids('stalled', false, 's1'), ['clear_stall', 'move']);
});
test("the confirm set is the bridge's CONFIRM (close, restart, park, fold) plus clear stall; wake and move ask nothing", () => {
  assert.deepEqual([...CONFIRM_VERBS].sort(), ['clear_stall', 'close', 'fold', 'park', 'restart', 'restart_fresh']);
});
test('builds each call from the verb', () => {
  assert.deepEqual(callOf(VERBS.close, 'a.b'), { method: 'session.stop', params: { agent: 'a.b', mode: 'close' } });
  assert.deepEqual(callOf(VERBS.restart_fresh, 'a.b'), { method: 'session.restart', params: { agent: 'a.b', fresh: true } });
  assert.deepEqual(callOf(VERBS.move, 'a.b', 'beta'), { method: 'agent.move', params: { agent: 'a.b', account: 'beta' } });
});

test('a structured agent lists interrupt only while a turn runs or waits', () => {
  const of = (state: string, session_open: boolean) =>
    verbsOf(row(state), { session_open, session_id: 's1', structured: true }).map((v) => v.id);
  assert.deepEqual(of('running', true), ['interrupt', 'restart', 'restart_fresh', 'close', 'park', 'fold', 'move']);
  assert.deepEqual(of('waiting', true), ['interrupt', 'restart', 'restart_fresh', 'close', 'park', 'fold', 'move']);
  assert.deepEqual(of('idle', true), ['restart', 'restart_fresh', 'close', 'park', 'fold', 'move']);
  assert.deepEqual(of('parked', false), ['wake', 'move']);
  assert.deepEqual(of('stalled', false), ['clear_stall', 'move']);
  assert.deepEqual(ids('running', true, 's1'), ['restart', 'restart_fresh', 'close', 'park', 'fold', 'move']); // a terminal agent has it in the stop menu
  assert.deepEqual(callOf(VERBS.interrupt, 'a.b'), { method: 'session.interrupt', params: { agent: 'a.b' } });
});

test('askOf: Close names the terminal that stays only for a terminal agent', () => {
  assert.match(askOf(VERBS.close, { structured: false })!, /terminal/);
  assert.doesNotMatch(askOf(VERBS.close, { structured: true })!, /terminal/);
  assert.equal(askOf(VERBS.park, { structured: true }), VERBS.park.ask);
});
