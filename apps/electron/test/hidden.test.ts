import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runsHidden } from '#src/hidden.ts';

test("an app an agent starts is hidden unless HANDLINE_SHOW says otherwise; the founder's is shown", () => {
  assert.equal(runsHidden({}), false);
  assert.equal(runsHidden({ HANDLINE_AGENT: 'handline.pc' }), true);
  assert.equal(runsHidden({ OCP_AGENT: 'handline.pc' }), true);
  assert.equal(runsHidden({ HANDLINE_AGENT: 'handline.pc', HANDLINE_SHOW: '1' }), false);
  assert.equal(runsHidden({ HANDLINE_AGENT: '' }), false);
  assert.equal(runsHidden({ HANDLINE_AGENT: 'handline', HANDLINE_FOLLOW: '1' }), false); // the founder's pnpm app, restarted by an agent
});
