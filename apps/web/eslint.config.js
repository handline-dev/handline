import reactHooks from 'eslint-plugin-react-hooks';
import { react, test } from '@handline/config/eslint';

// React Compiler's rules as warnings: a component it has to skip is reported, not refused (the compiler builds the rest).
const compiler = Object.fromEntries(
  Object.keys(reactHooks.configs['recommended-latest'].rules)
    .filter((r) => r !== 'react-hooks/rules-of-hooks' && r !== 'react-hooks/exhaustive-deps')
    .map((r) => [r, 'warn']),
);
// Layering: a view holds no data hook and no label table (the rule sees only a literal Record<string, string>). Screens are listed here as they are split into a hook and a view (T6245); the list only grows.
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
export default [...react(import.meta.dirname), { files: ['src/**'], rules: compiler }, ...views, ...test];
