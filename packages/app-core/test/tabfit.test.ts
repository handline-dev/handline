import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fitTabs } from '#src/index.ts';

const ids = ['chat', 'a', 'b', 'c', 'd'];

test('every tab shows while they fit', () => {
  assert.deepEqual(fitTabs(ids, 'a', 5), { shown: ids, more: [] });
  assert.deepEqual(fitTabs(ids, 'a', 9), { shown: ids, more: [] });
});

test('what does not fit goes to the menu, in order', () => {
  assert.deepEqual(fitTabs(ids, 'a', 3), { shown: ['chat', 'a', 'b'], more: ['c', 'd'] });
});

test('the active tab that does not fit takes the place of the last shown one', () => {
  assert.deepEqual(fitTabs(ids, 'd', 3), { shown: ['chat', 'a', 'd'], more: ['b', 'c'] });
});

test('there is always room for one tab, and an unknown active tab changes nothing', () => {
  assert.deepEqual(fitTabs(ids, 'c', 0), { shown: ['c'], more: ['chat', 'a', 'b', 'd'] });
  assert.deepEqual(fitTabs(ids, 'zzz', 2), { shown: ['chat', 'a'], more: ['b', 'c', 'd'] });
});
