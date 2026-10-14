import { defineConfig } from 'vitest/config';

// The screens are React Native; the tests draw them with react-native-web in a DOM, against the fake daemon. The expo image modules are native.
export default defineConfig({
  resolve: {
    alias: {
      'react-native': 'react-native-web',
      'expo-image-picker': new URL('./test/stubs/expo-image.ts', import.meta.url).pathname,
      'expo-image-manipulator': new URL('./test/stubs/expo-image.ts', import.meta.url).pathname,
    },
  },
  test: { environment: 'happy-dom', include: ['test/**/*.test.tsx'] },
});
