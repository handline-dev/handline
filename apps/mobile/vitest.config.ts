import { defineConfig } from 'vitest/config';

// The screens are React Native; the tests draw them with react-native-web in a DOM, against the fake daemon. The expo image modules are native.
export default defineConfig({
  resolve: {
    alias: [
      { find: /^react-native-svg$/, replacement: new URL('./test/svg-stub.tsx', import.meta.url).pathname },
      { find: 'lucide-react-native', replacement: 'lucide-react' },
      { find: 'react-native-view-shot', replacement: new URL('./test/view-shot-stub.ts', import.meta.url).pathname },
      { find: 'react-native-webview', replacement: new URL('./test/webview-stub.tsx', import.meta.url).pathname },
      { find: 'react-native', replacement: 'react-native-web' },
      { find: 'expo-image-picker', replacement: new URL('./test/expo-image-stub.tsx', import.meta.url).pathname },
      { find: 'expo-image-manipulator', replacement: new URL('./test/expo-image-stub.tsx', import.meta.url).pathname },
      { find: /^.*modules\/apk-update\/index\.ts$/, replacement: new URL('./test/apk-update-stub.ts', import.meta.url).pathname },
      { find: 'expo-secure-store', replacement: new URL('./test/secure-store-stub.ts', import.meta.url).pathname },
    ],
  },
  test: { environment: 'happy-dom', include: ['test/**/*.test.tsx'], maxWorkers: 2 }, // several teams' runs share one machine
});
