import { react, test } from '@handline/config/eslint';

// scripts/ and test/ run under Node and sit outside the app's tsconfig, so they are checked against their own.
const node = { projectService: false, project: './tsconfig.scripts.json', tsconfigRootDir: import.meta.dirname };
export default [
  { ignores: ['plugins/'] }, // Expo config plugins are CommonJS, read by the Expo CLI
  ...react(import.meta.dirname),
  ...test,
  { files: ['scripts/*.ts', 'test/*.ts', 'test/*.tsx'], languageOptions: { parserOptions: node } },
];
