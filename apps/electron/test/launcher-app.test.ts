import { test } from 'node:test';
import assert from 'node:assert/strict';
import { infoPlist, launcherScript, LAUNCHER_ID, relaunchCommand } from '#src/launcher-app.ts';

test('the launcher bundle is named Handline, has no Dock icon of its own and carries the icon', () => {
  const p = infoPlist();
  assert.match(p, /<key>CFBundleExecutable<\/key><string>Handline<\/string>/);
  assert.match(p, new RegExp(`<string>${LAUNCHER_ID}</string>`));
  assert.match(p, /<key>LSUIElement<\/key><true\/>/);
  assert.match(p, /<key>CFBundleIconFile<\/key><string>Handline<\/string>/);
});

test('the launcher asks the login shell for a PATH, starts follow-start from the checkout in the background and logs', () => {
  const s = launcherScript();
  assert.match(s, /^#!\/bin\/bash/);
  assert.match(s, /-ilc 'echo __PATH__\$PATH'/);
  assert.match(s, /node apps\/electron\/follow-start\.ts/);
  assert.match(s, /HANDLINE_SOURCE_GIT=/);
  assert.match(s, /launcher\.log/);
  assert.match(s, /&\n$/, 'ends with the start in the background, so the next open runs the script again');
});

test('a second open brings the running Handline bundle to the front instead of starting another', () => {
  const s = launcherScript();
  const pattern = /pgrep -f '([^']+)'/.exec(s)?.[1] ?? '';
  const re = new RegExp(pattern);
  assert.ok(re.test('/home/user/.local/share/handline/Handline-38.8.6.app/Contents/MacOS/Electron .'));
  assert.ok(re.test('/home/user/.local/share/handline/Handline-38.8.6.app/Contents/MacOS/Electron'));
  assert.ok(
    !re.test(
      '/home/user/.local/share/handline/Handline-38.8.6.app/Contents/Frameworks/Handline Helper (GPU).app/Contents/MacOS/Electron Helper (GPU)',
    ),
  );
  assert.ok(!re.test('/bin/bash /Applications/Handline.app/Contents/MacOS/Handline'), 'the launcher does not find itself');
  assert.ok(s.indexOf('open "') < s.indexOf('follow-start'), 'the check comes first');
});

test('the relaunch waits for the old process to end, then runs launch.ts from the electron folder', () => {
  const c = relaunchCommand(123, '/w/apps/electron');
  assert.equal(c.file, 'sh');
  assert.deepEqual(c.args.slice(2), ['sh', '123', '/w/apps/electron']);
  assert.match(c.args[1]!, /kill -0 "\$1"[^;]*; do sleep[^;]*; done; cd "\$2" && exec node launch\.ts/);
});
