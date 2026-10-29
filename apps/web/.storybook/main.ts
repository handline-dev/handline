import type { StorybookConfig } from '@storybook/react-vite';

// The design system's parts (src/ds) one story per part and state, checked against the spec page. `pnpm storybook` serves it.
const config: StorybookConfig = {
  stories: ['../src/ds/*.stories.tsx'],
  framework: '@storybook/react-vite',
  core: { disableTelemetry: true },
};
export default config;
