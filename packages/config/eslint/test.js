import { limits } from './limits.js';

const skip = { skipBlankLines: true, skipComments: true };

/** Tests: a longer file, and no function limit (a describe callback is one long function by design). */
export const test = [
  {
    files: ['**/test/**', '**/*.test.ts', '**/*.test.tsx'],
    rules: { 'max-lines': ['error', { max: limits.testFileLines, ...skip }], 'max-lines-per-function': 'off' },
  },
];
