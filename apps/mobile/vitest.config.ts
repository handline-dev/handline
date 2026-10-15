import { defineConfig } from 'vitest/config';

// The screens are React Native; the tests draw them with react-native-web in a DOM, against the fake daemon. The expo image modules are native.
export default defineConfig({
  resolve: {
    alias: [
      { find: /^react-native-svg$/, replacement: new URL('./test/svg-stub.tsx', import.meta.url).pathname },
      { find: 'react-native', replacement: 'react-native-web' },
      { find: 'expo-image-picker', replacement: new URL('./test/expo-image-stub.tsx', import.meta.url).pathname },
      { find: 'expo-image-manipulator', replacement: new URL('./test/expo-image-stub.tsx', import.meta.url).pathname },
    ],
  },
  test: { environment: 'happy-dom', include: ['test/**/*.test.tsx'] },
});
