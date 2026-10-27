import reactHooks from 'eslint-plugin-react-hooks';
import query from '@tanstack/eslint-plugin-query';
import globals from 'globals';
import { base } from '#eslint/base.js';

export const react = (dir) => [
  ...base(dir),
  // React Compiler's rules (the `recommended-latest` set) plus two it leaves out; the experimental ones outside `recommended` stay off.
  {
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...Object.fromEntries(Object.keys(reactHooks.configs['recommended-latest'].rules).map((r) => [r, 'error'])),
      'react-hooks/component-hook-factories': 'error',
      'react-hooks/rule-suppression': 'error',
    },
  },
  ...query.configs['flat/recommended'],
  { languageOptions: { globals: globals.browser } },
];
