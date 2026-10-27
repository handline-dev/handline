import { react, test } from '@handline/config/eslint';
// Compiler debt: these files fail refs today and stay at warn. The list only shrinks; a file not in it gets the error.
const compilerDebt = {
  files: ['src/index.ts'],
  rules: { 'react-hooks/refs': 'warn' },
};
export default [...react(import.meta.dirname), ...test, compilerDebt];
