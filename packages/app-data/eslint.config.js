import { react, test } from '@handline/config/eslint';
// Compiler debt: these files fail set-state-in-effect or refs today and stay at warn. The list only shrinks; a file not in it gets the error.
const compilerDebt = {
  files: ['src/attachments.ts', 'src/file-editor.ts', 'src/prompt-answer.ts', 'src/write.ts'],
  rules: { 'react-hooks/set-state-in-effect': 'warn', 'react-hooks/refs': 'warn' },
};
export default [...react(import.meta.dirname), ...test, compilerDebt];
