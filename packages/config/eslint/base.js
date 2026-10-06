import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import { limits } from './limits.js';

const skip = { skipBlankLines: true, skipComments: true };
/** Folder boundaries from docs/turborepo-layout.md. A rule's options are replaced, not merged, so each scope lists all its patterns. */
const REACT = ['react', 'react-dom', 'react-native'].map((name) => ({ name, message: 'This package must not depend on React.' }));
const ALL = [
  { group: ['@handline/*/src/*'], message: 'Import the package entry, not its src.' },
  { group: ['@tanstack/react-query'], message: 'React Query lives in packages/app-data.' },
];
const NO_APPS = { group: ['**/apps/**', '**/web/**'], message: 'Apps are never imported.' };
const NO_APP_PKGS = { group: ['@handline/app-*'], message: 'This package sits below the app packages.' };
const NO_FAKE = {
  group: ['@handline/fake-daemon', '@handline/fake-daemon/*'],
  message: 'The fake daemon is for tests and vite.config.ts only.',
};
const scope = (files, paths, patterns, ignores = []) => ({
  files,
  ignores,
  rules: { 'no-restricted-imports': ['error', { paths, patterns: [...ALL, ...patterns] }] },
});

export const ignores = {
  ignores: ['**/dist/**', '**/.turbo/**', '**/node_modules/**', 'contract/protocol.ts', 'contract/fixtures/**', 'contract/prompts/**'],
};

export const base = [
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
  scope(['packages/client/**', 'packages/tokens/**'], REACT, [NO_APPS, NO_APP_PKGS]),
  scope(['packages/app-core/**'], REACT, [NO_APPS]),
  scope(['packages/app-react/**', 'packages/client-node/**', 'packages/fake-daemon/**', 'packages/config/**'], [], [NO_APPS]),
  scope(['apps/**'], [], [NO_APPS, NO_FAKE], ['apps/*/test/**', 'apps/*/vite.config.ts']),
  scope(['web/**'], [], []),
];
