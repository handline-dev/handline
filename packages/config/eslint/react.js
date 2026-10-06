import reactHooks from 'eslint-plugin-react-hooks';
import query from '@tanstack/eslint-plugin-query';
import globals from 'globals';
import { base } from './base.js';

export const react = (dir) => [
  ...base(dir),
  // The two rules-of-hooks rules only; the compiler rules in `recommended` ask for refactors of working code.
  { plugins: { 'react-hooks': reactHooks }, rules: { 'react-hooks/rules-of-hooks': 'error', 'react-hooks/exhaustive-deps': 'error' } },
  ...query.configs['flat/recommended'],
  { languageOptions: { globals: globals.browser } },
];
