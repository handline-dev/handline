import assert from 'node:assert/strict';
import test from 'node:test';
import { COPY_URL, phoneHtml } from '#src/phone-html.ts';
import { trayMenu } from '#src/tray.ts';

test('the phone page holds a QR, a copy button, the lifetime and no script, and the link itself is not printed', () => {
  const html = phoneHtml('handline://pair?host=h.ts.net%3A7443&code=AAAA-BBBB-CCCC', 5, '10:42');
  assert.match(html, /<svg/);
  assert.ok(html.includes(`href="${COPY_URL}"`));
  assert.match(html, /5 minutes, until 10:42/);
  assert.doesNotMatch(html, /<script|AAAA-BBBB-CCCC/);
});

test('Pair a phone is in the tray only when the app has a way to show it', () => {
  const link = { status: 'connected' as const, address: 'a', version: null, reason: '', mismatch: () => null };
  const labels = (phone?: () => void) =>
    trayMenu('running', '', { open() {}, startDaemon() {}, quit() {}, inbox() {}, settings() {}, reconnect() {}, copy() {} }, undefined, {
      mode: 'own',
      link,
      switchTo() {},
      pair() {},
      phone,
    }).map((i) => i.label);
  assert.ok(labels(() => {}).includes('Pair a phone…'));
  assert.ok(!labels().includes('Pair a phone…'));
});
