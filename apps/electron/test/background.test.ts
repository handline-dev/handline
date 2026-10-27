import assert from 'node:assert/strict';
import { test } from 'node:test';
import { drawsTray } from '#src/background.ts';

test('the packaged app and the follow-main checkout draw the menu bar icon; a dev or an agent run does not', () => {
  assert.equal(drawsTray({}, true), true);
  assert.equal(drawsTray({ HANDLINE_FOLLOW: '1' }, false), true);
  assert.equal(drawsTray({}, false), false);
  assert.equal(drawsTray({ OCP_AGENT: 'handline.pc' }, true), false);
  assert.equal(drawsTray({ HANDLINE_BACKGROUND: '1', HANDLINE_FOLLOW: '1' }, false), false);
});

test('HANDLINE_TRAY=1 asks for the icon in any run', () => {
  assert.equal(drawsTray({ HANDLINE_TRAY: '1' }, false), true);
  assert.equal(drawsTray({ HANDLINE_TRAY: '1', OCP_AGENT: 'a' }, false), true);
});
