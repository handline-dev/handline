import { test } from 'node:test';
import assert from 'node:assert/strict';
import { routeOfTarget } from '#src/shell.ts';

test('a target lands on the route its screen names, with the id encoded', () => {
  assert.equal(routeOfTarget({ screen: 'agent', id: 'handline.architect' }), '#/agents/handline.architect');
  assert.equal(routeOfTarget({ screen: 'agent', id: 'a/b c' }), '#/agents/a%2Fb%20c');
  assert.equal(routeOfTarget({ screen: 'account', id: 'alpha' }), '#/accounts/alpha');
});

test('a task event, finished or not, lands on the task', () => {
  assert.equal(routeOfTarget({ screen: 'hr', id: '5' }), '#/team/hr/5');
  assert.equal(routeOfTarget({ screen: 'hr', id: 'None' }), null);
  assert.equal(routeOfTarget({ screen: 'task', id: '7' }), '#/tasks/7');
});

test('no target, an empty id, the id "None" or a screen it does not know is no route', () => {
  assert.equal(routeOfTarget(null), null);
  assert.equal(routeOfTarget({ screen: 'agent', id: '' }), null);
  assert.equal(routeOfTarget({ screen: 'task', id: 'None' }), null);
  assert.equal(routeOfTarget({ screen: 'galaxy', id: 'x' }), null);
});
