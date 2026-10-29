import type { Preview } from '@storybook/react-vite';
import '../src/styles.css';

// The toolbar's theme sets data-theme on <html>, the same switch the app uses, so every story shows in light and dark.
const preview: Preview = {
  globalTypes: {
    theme: { description: 'Theme', toolbar: { icon: 'circlehollow', items: ['light', 'dark'], dynamicTitle: true } },
  },
  initialGlobals: { theme: 'light' },
  decorators: [
    (Story, { globals }) => {
      document.documentElement.dataset.theme = globals.theme;
      return (
        <div className="bg-canvas p-6 font-sans text-md text-ink">
          <Story />
        </div>
      );
    },
  ],
  parameters: { layout: 'fullscreen', controls: { expanded: true } },
};
export default preview;
