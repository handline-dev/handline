// Bundles the main process to dist/main.cjs: Electron runs CommonJS, and our packages are TypeScript source.
import { build } from 'esbuild';

await build({
  entryPoints: ['src/main.ts'], outfile: 'dist/main.cjs', bundle: true, platform: 'node', format: 'cjs',
  target: 'node22', external: ['electron'], logLevel: 'info',
  define: { 'import.meta.dirname': '__dirname' },
});
