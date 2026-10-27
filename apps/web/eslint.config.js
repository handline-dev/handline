import reactHooks from 'eslint-plugin-react-hooks';
import { react, test } from '@handline/config/eslint';

// React Compiler's rules as warnings: a component it has to skip is reported, not refused (the compiler builds the rest).
const compiler = Object.fromEntries(
  Object.keys(reactHooks.configs['recommended-latest'].rules)
    .filter((r) => r !== 'react-hooks/rules-of-hooks' && r !== 'react-hooks/exhaustive-deps')
    .map((r) => [r, 'warn']),
);
export default [...react(import.meta.dirname), { files: ['src/**'], rules: compiler }, ...test];
