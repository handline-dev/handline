// Bundles the main process to dist/main.cjs and the preload to dist/preload.cjs: Electron runs CommonJS, and our packages are TypeScript source.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import { build, type BuildOptions } from 'esbuild';

const common: BuildOptions = {
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node22',
  external: ['electron'],
  logLevel: 'info',
  define: { 'import.meta.dirname': '__dirname', 'import.meta.filename': '__filename' },
};

await build({ ...common, entryPoints: ['src/main.ts'], outfile: 'dist/main.cjs' });
await build({ ...common, entryPoints: ['src/preload.ts'], outfile: 'dist/preload.cjs' });

// The build id the app and the daemon are compared by: the 12-char git sha of the checkout (src/build-id.ts reads it).
let sha = 'unknown';
try {
  sha = execFileSync('git', ['rev-parse', '--short=12', 'HEAD'], { encoding: 'utf8' }).trim();
} catch {
  /* not a git checkout: the build has no id, and no mismatch is told */
}
fs.writeFileSync('dist/build.json', JSON.stringify({ sha }) + '\n');
