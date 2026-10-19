import { defineConfig } from 'vitest/config';

// maxWorkers: several teams' runs share one machine
export default defineConfig({ test: { environment: 'happy-dom', include: ['test/**/*.test.{ts,tsx}'], maxWorkers: 2 } });
