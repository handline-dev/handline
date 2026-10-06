import { test } from 'node:test';
import assert from 'node:assert/strict';
import { routeOfTarget } from '../src/shell.ts';

test('a target lands on the route its screen names, with the id encoded', () => {
  assert.equal(routeOfTarget({ screen: 'agent', id: 'handline.architect' }, 'prompt_asked'), '#/agents/handline.architect');
  assert.equal(routeOfTarget({ screen: 'agent', id: 'a/b c' }, 'stall'), '#/agents/a%2Fb%20c');
  assert.equal(routeOfTarget({ screen: 'account', id: 'alpha' }, 'account_out'), '#/accounts/alpha');
});

test('a finished task lands on the inbox, any other task event on the task', () => {
  assert.equal(routeOfTarget({ screen: 'task', id: '7' }, 'task_done'), '#/inbox');
  assert.equal(routeOfTarget({ screen: 'task', id: '7' }, 'task_blocked'), '#/tasks/7');
});

test('no target, an empty id, the id "None" or a screen it does not know is no route', () => {
  assert.equal(routeOfTarget(null, 'stall'), null);
  assert.equal(routeOfTarget({ screen: 'agent', id: '' }, 'stall'), null);
  assert.equal(routeOfTarget({ screen: 'task', id: 'None' }, 'task_done'), null);
  assert.equal(routeOfTarget({ screen: 'galaxy', id: 'x' }, 'stall'), null);
});
