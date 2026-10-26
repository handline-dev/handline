// The installable app: Handline.app in a dmg and a zip, universal (arm64 + x64). Sign with a Developer ID by setting HANDLINE_SIGN_IDENTITY
// to its name (and, for notarization, the usual APPLE_* variables electron-builder reads); with none, the app is ad-hoc signed, which is enough
// to run on this Mac and is all there is without an Apple developer account.
import { createRequire } from 'node:module';
import process from 'node:process';

const developerId = !!process.env.HANDLINE_SIGN_IDENTITY;

export default {
  appId: 'dev.handline.app',
  // electron-builder cannot read the version from a range under pnpm.
  electronVersion: createRequire(import.meta.url)('electron/package.json').version,
  productName: 'Handline',
  directories: { output: 'release' },
  files: ['dist/**', 'assets/**', 'package.json'],
  // The web build, and the daemon and CLI that `tools/build-darwin.sh` made: main.ts finds them under Resources when the app is packaged.
  extraResources: [
    { from: '../web/dist', to: 'web' },
    { from: '../../dist/darwin/handlined', to: 'bin/handlined' },
    { from: '../../dist/darwin/hl', to: 'bin/hl' },
  ],
  npmRebuild: false,
  afterSign: 'ad-hoc-sign.mjs',
  mac: {
    category: 'public.app-category.developer-tools',
    icon: 'assets/icon.png',
    // The daemon and CLI are universal already: the same file in both builds is what they should be.
    x64ArchFiles: 'Contents/Resources/bin/*',
    target: [
      { target: 'dmg', arch: 'universal' },
      { target: 'zip', arch: 'universal' },
    ],
    // No identity: electron-builder leaves the app unsigned and the afterSign hook signs it ad hoc.
    identity: developerId ? process.env.HANDLINE_SIGN_IDENTITY : null,
    hardenedRuntime: developerId,
    notarize: false,
    entitlements: developerId ? 'assets/entitlements.mac.plist' : undefined,
    entitlementsInherit: developerId ? 'assets/entitlements.mac.plist' : undefined,
  },
  dmg: { sign: false },
};
