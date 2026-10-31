import { react, test } from '@handline/config/eslint';

// scripts/ and test/ run under Node and sit outside the app's tsconfig, so they are checked against their own.
const node = { projectService: false, project: './tsconfig.scripts.json', tsconfigRootDir: import.meta.dirname };
// Compiler debt: these files fail set-state-in-effect, refs, purity or static-components today and stay at warn. The list only shrinks; a file not in it gets the error.
const compilerDebt = {
  files: [
    'src/screens/AccountSettings.tsx',
    'src/screens/Accounts.tsx',
    'src/screens/AgentStrip.tsx',
    'src/screens/AgentTasks.tsx',
    'src/screens/ChatScroll.ts',
    'src/screens/ChatSend.tsx',
    'src/screens/HandoffPrompt.tsx',
    'src/screens/Home.tsx',
    'src/screens/Icon.tsx',
    'src/screens/ImageBodyInput.tsx',
    'src/screens/MarkPicture.tsx',
    'src/screens/PromptCard.tsx',
    'src/screens/Settings.tsx',
    'src/screens/StateMark.tsx',
    'src/screens/Tasks.tsx',
    'src/screens/TerminalPane.tsx',
    'src/skeleton.tsx',
  ],
  rules: {
    'react-hooks/set-state-in-effect': 'warn',
    'react-hooks/refs': 'warn',
    'react-hooks/purity': 'warn',
    'react-hooks/static-components': 'warn',
  },
};
export default [
  { ignores: ['plugins/'] }, // Expo config plugins are CommonJS, read by the Expo CLI
  ...react(import.meta.dirname),
  ...test,
  compilerDebt,
  { files: ['scripts/*.ts', 'test/*.ts', 'test/*.tsx'], languageOptions: { parserOptions: node } },
];
