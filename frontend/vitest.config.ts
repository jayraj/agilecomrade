import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Test-only config. Kept separate from vite.config.ts so the PWA plugin and
// app build settings don't run under the test runner.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
  },
})