import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inviteLink } from '#src/index.ts';

test('the invite link is the gateway address with the code in the fragment, https only with a certificate', () => {
  const uri = 'handline://pair?host=mac.tail1.ts.net%3A7443&fp=ab&code=K7QM-2XPD';
  assert.equal(inviteLink(uri, false), 'http://mac.tail1.ts.net:7443/#pair=K7QM-2XPD');
  assert.equal(inviteLink(uri, true), 'https://mac.tail1.ts.net:7443/#pair=K7QM-2XPD');
});

test('no link for a gateway on this computer only, a line that is not a pairing line, or a missing part', () => {
  assert.equal(inviteLink('handline://pair?host=127.0.0.1%3A7443&code=A', false), null);
  assert.equal(inviteLink('handline://pair?host=localhost%3A7443&code=A', false), null);
  assert.equal(inviteLink('https://x.example/?host=a&code=b', false), null);
  assert.equal(inviteLink('handline://pair?host=a.ts.net%3A7443', false), null);
});
