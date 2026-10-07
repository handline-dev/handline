// Bundles the main process to dist/main.cjs and the preload to dist/preload.cjs: Electron runs CommonJS, and our packages are TypeScript source.
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
await build({ ...common, entryPoints: ['src/popover-preload.ts'], outfile: 'dist/popover-preload.cjs' });
