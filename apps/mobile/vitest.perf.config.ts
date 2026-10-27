import { defineConfig } from 'vitest/config';
import { transformAsync } from '@babel/core';
import base from './vitest.config.ts';
const compiler = process.env.PERF_TAG === 'compiler';
export default defineConfig({
  resolve: (base as any).resolve,
  root: '/Users/ron/workspaces/orcapipe.worktrees/handline.mobile/apps/mobile',
  plugins: compiler ? [{
    name: 'rc', enforce: 'pre',
    async transform(code: string, id: string) {
      if (!/\/apps\/mobile\/src\/.*\.tsx?$/.test(id) || !new RegExp(process.env.PERF_ONLY ?? '.').test(id)) return null;
      const r = await transformAsync(code, { filename: id, babelrc: false, configFile: false, plugins: [['babel-plugin-react-compiler', {}]], parserOpts: { plugins: ['typescript', 'jsx'] }, sourceMaps: true });
      return r?.code ? { code: r.code, map: r.map } : null;
    },
  }] : [],
  test: { ...(base as any).test, include: (process.env.PERF_INCLUDE ?? 'test/zz-perf.test.tsx').split(','), maxWorkers: 1, testTimeout: 120000 },
});
