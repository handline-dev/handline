import { defineConfig } from 'vitest/config';

// The screens are React Native; the tests draw them with react-native-web in a DOM, against the fake daemon.
export default defineConfig({
  resolve: {
    alias: [
      { find: /^react-native-svg$/, replacement: new URL('./test/svg-stub.tsx', import.meta.url).pathname },
      { find: 'react-native', replacement: 'react-native-web' },
    ],
  },
  test: { environment: 'happy-dom', include: ['test/**/*.test.tsx'] },
});
