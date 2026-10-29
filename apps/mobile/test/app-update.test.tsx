import { beforeEach, describe, expect, it, vi } from 'vitest';
import { $, mount, press, text, until } from '#test/harness.tsx';
import { AppUpdate, UpdateBanner } from '#src/screens/AppUpdate.tsx';

// The update module is Android-native and the release check goes to GitHub; the test stands in for both.
const native = vi.hoisted(() => ({
  installedVersionCode: () => 100,
  installedVersionName: () => '0.0.0-nightly.100',
  canInstall: vi.fn(() => true),
  openInstallSettings: vi.fn(),
  downloadAndInstall: vi.fn(() => Promise.resolve()),
}));
const check = vi.hoisted(() => vi.fn());
vi.mock('#modules/apk-update/index.ts', () => ({ default: native }));
vi.mock('#src/update.ts', () => ({ checkForUpdate: check }));

const NEWER = { versionName: '0.0.0-nightly.101', versionCode: 101, url: 'https://example.test/101.apk' };

beforeEach(() => {
  vi.clearAllMocks();
  native.canInstall.mockReturnValue(true);
  check.mockResolvedValue(NEWER);
});

describe('the app update', () => {
  it('shows a banner for a newer build of the installed channel, and Install hands it to the installer', async () => {
    const m = await mount(undefined, undefined, undefined, { screen: () => <UpdateBanner /> });
    await until(() => $('update-banner'), 'the banner');
    expect(check).toHaveBeenCalledWith('nightly', 100);
    expect(text($('update-banner'))).toContain('Update 0.0.0-nightly.101 is ready');
    await press($('update-banner-install'));
    expect(native.downloadAndInstall).toHaveBeenCalledWith(NEWER.url);
    m.store.stop();
  });

  it('opens the install permission first when the app may not install yet, and downloads nothing', async () => {
    native.canInstall.mockReturnValue(false);
    const m = await mount(undefined, undefined, undefined, { screen: () => <UpdateBanner /> });
    await until(() => $('update-banner'), 'the banner');
    await press($('update-banner-install'));
    expect(native.openInstallSettings).toHaveBeenCalledOnce();
    expect(native.downloadAndInstall).not.toHaveBeenCalled();
    m.store.stop();
  });

  it('draws no banner when the channel has nothing newer, and settings say so', async () => {
    check.mockResolvedValue(null);
    const m = await mount(undefined, undefined, undefined, {
      screen: () => (
        <>
          <UpdateBanner />
          <AppUpdate />
        </>
      ),
    });
    await until(() => text($('update-status')) === 'You have the newest build of this channel.', 'the status');
    expect($('update-banner')).toBeNull();
    m.store.stop();
  });

  it('says why a failed check did not update, and Stable checks that channel', async () => {
    check.mockRejectedValueOnce(new Error('GitHub did not answer')).mockResolvedValue(null);
    const m = await mount(undefined, undefined, undefined, { screen: () => <AppUpdate /> });
    await until(() => text($('update-status')) === 'Not updated: GitHub did not answer', 'the error');
    await press($('channel-stable'));
    await until(() => check.mock.calls.some((c) => c[0] === 'stable'), 'the stable check');
    await until(() => text($('update-status')) === 'You have the newest build of this channel.', 'the stable answer');
    m.store.stop();
  });
});
