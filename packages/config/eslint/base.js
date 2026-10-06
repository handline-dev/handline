import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import { boundaries } from './boundaries.js';
import { limits } from './limits.js';

const skip = { skipBlankLines: true, skipComments: true };
export const ignores = {
  ignores: ['**/dist/**', '**/.turbo/**', '**/node_modules/**', 'contract/protocol.ts', 'contract/fixtures/**', 'contract/prompts/**'],
};

/** The shared rules for the package whose `eslint.config.js` passes its own directory. */
export const base = (dir) => [
  ignores,
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    linterOptions: { reportUnusedDisableDirectives: 'error' },
    languageOptions: { parserOptions: { projectService: true } },
    rules: {
      'max-lines': ['error', { max: limits.fileLines, ...skip }],
      'max-lines-per-function': ['error', { max: limits.functionLines, IIFEs: true, ...skip }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_', ignoreRestSiblings: true }],
      'max-depth': ['error', limits.depth],
      '@typescript-eslint/no-floating-promises': [
        'error',
        { allowForKnownSafeCalls: [{ from: 'package', package: 'node:test', name: ['test', 'describe', 'it'] }] },
      ],
      '@typescript-eslint/no-misused-promises': 'error',
      '@typescript-eslint/consistent-type-imports': 'error',
    },
  },
  {
    files: ['**/*.tsx'],
    rules: { 'max-lines-per-function': ['error', { max: limits.componentLines, IIFEs: true, ...skip }] },
  },
  {
    files: ['**/*.js', '**/*.mjs', '**/*.cjs'],
    ...tseslint.configs.disableTypeChecked,
  },
  ...boundaries(dir),
];
