import { react, test } from '@handline/config/eslint';

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
export default [...react(import.meta.dirname), ...views, ...test];
