import { react, test } from '@handline/config/eslint';

// Compiler debt: these files fail set-state-in-effect or refs today and stay at warn. The list only shrinks; a file not in it gets the error.
const compilerDebt = {
  files: [
    'src/AccountSettings.tsx',
    'src/Annotate.tsx',
    'src/App.tsx',
    'src/BodyEditor.tsx',
    'src/Capture.tsx',
    'src/ChatTab.tsx',
    'src/DeviceTab.tsx',
    'src/HandoffPrompt.tsx',
    'src/Pane.tsx',
    'src/PromptCard.tsx',
    'src/SettingsRows.tsx',
    'src/TaskDetail.tsx',
    'src/Tasks.tsx',
    'src/TerminalTab.tsx',
    'src/WebTab.tsx',
    'src/Workspace.tsx',
    'src/chatLines.ts',
    'src/ui/Dialog.tsx',
    'src/ui/Menu.tsx',
  ],
  rules: { 'react-hooks/set-state-in-effect': 'warn', 'react-hooks/refs': 'warn' },
};
// Layering: a view holds no data hook and no label table. Screens are listed here as they are split into a hook and a view (T6245); the list only grows.
const views = [
  {
    files: ['src/AgentRow.tsx', 'src/Pane.tsx'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: "ImportDeclaration[source.value='@handline/app-data']",
          message: 'A view reads no data: take it from the screen hook (AgentRowData.ts, PaneData.ts).',
        },
        {
          selector:
            "TSTypeReference[typeName.name='Record'] > TSTypeParameterInstantiation[params.0.type='TSStringKeyword'][params.1.type='TSStringKeyword']",
          message: 'A label table belongs in app-core, not in a component.',
        },
      ],
    },
  },
];
export default [...react(import.meta.dirname), compilerDebt, ...views, ...test];
